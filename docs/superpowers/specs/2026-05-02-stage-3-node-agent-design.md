# Stage 3 — node-agent

**Дата:** 2026-05-02
**Источник ТЗ:** `claude_code_prompt.md` (§3, §6 node-agent API, §7 безопасность контейнеров, §9 Stage 3)
**Предыдущий этап:** Stage 2 (templates + nodes admin) — PR #3, в `main`.

## Context

Stage 1–2 построили публичный API (auth, users, templates, nodes). На Stage 4 worker должен поднимать контейнеры на физических нодах. Чтобы ему было кому давать команды, нужен **node-agent** — отдельный FastAPI-сервис, который живёт на каждой ноде, разговаривает с локальным Docker daemon через SDK и не имеет доступа к основной БД. Stage 3 строит этот сервис изолированно: lifecycle контейнеров (create/start/stop/restart/delete), статус/статистика, SSE-стрим логов, healthz, Bearer-API-key авторизация, security defaults (`cap_drop=ALL`, `no-new-privileges`, лимиты CPU/mem). Backup/exec эндпоинт переносится в Stage 7.

### Решённые ключевые развилки

- **Scope (ответ A):** только lifecycle + status + logs + healthz. `POST /containers/{id}/exec` для бэкапов — Stage 7.
- **Стратегия тестов (ответ C):** mock Docker SDK для domain/routes, один реальный smoke-тест с `busybox:latest` под маркером `pytest -m docker_real` (skip по умолчанию).
- **API-key (ответ A):** plaintext в env (`NODE_AGENT_API_KEY`), constant-time compare. Хеш/файл-секреты — отложено.
- **`CamelModel` переезжает в `packages/shared`,** чтобы node-agent его не дублировал.

## Структура каталогов

```
apps/node_agent/
├── pyproject.toml                       # gamehost-node, член uv workspace
├── Dockerfile                           # multi-stage, монтирует /var/run/docker.sock
├── src/gamehost_node/
│   ├── __init__.py
│   ├── main.py                          # FastAPI app + lifespan
│   ├── core/
│   │   ├── config.py                    # NODE_AGENT_API_KEY, DOCKER_HOST, LOG_LEVEL, DEFAULT_NETWORK
│   │   ├── auth.py                      # Bearer middleware (constant-time compare)
│   │   ├── logging.py                   # structlog config
│   │   └── errors.py                    # NodeAgentError + RFC 7807 handler
│   ├── domain/
│   │   ├── exceptions.py                # ContainerNotFound, ContainerNameTaken, DockerUnavailable
│   │   └── containers.py                # ContainerService
│   ├── docker_facade.py                 # обёртка над Docker SDK
│   ├── api/
│   │   ├── __init__.py                  # router /api/v1
│   │   ├── containers.py                # /containers/*
│   │   └── health.py                    # /healthz
│   └── schemas/
│       └── containers.py                # CreateContainerIn, ContainerOut, ContainerStatsOut
└── tests/
    ├── conftest.py                      # AsyncClient + mock DockerFacade fixture
    ├── factories.py
    ├── test_auth.py
    ├── test_containers_routes_mock.py
    ├── test_containers_service_mock.py
    └── test_docker_real.py              # @pytest.mark.docker_real

packages/shared/src/gamehost_shared/
└── camel_model.py                       # переехал из apps/api/src/gamehost_api/schemas/base.py
```

`apps/api/src/gamehost_api/schemas/base.py` становится re-export'ом `from gamehost_shared.camel_model import CamelModel` для совместимости (или прямой импорт в местах использования; см. план).

## Эндпоинты (`/api/v1/*`)

Все требуют `Authorization: Bearer <NODE_AGENT_API_KEY>`, кроме `/healthz`. Ответы JSON camelCase. Ошибки RFC 7807 (`application/problem+json`).

| Метод/путь | Поведение | Ошибки |
|---|---|---|
| `GET /healthz` | 200 `{"status":"ok"}`. Без auth. | — |
| `POST /containers` | Создаёт контейнер по `CreateContainerIn` со security defaults и сразу стартует. Возвращает `ContainerOut`. | 401 missing_bearer; 409 container_name_taken; 503 docker_unavailable |
| `GET /containers/{id}` | `ContainerOut + ContainerStats`. | 404 container_not_found |
| `POST /containers/{id}/start` | Идемпотентно. | 404, 503 |
| `POST /containers/{id}/stop` | Идемпотентно, timeout 30s. | 404, 503 |
| `POST /containers/{id}/restart` | Stop+start. | 404, 503 |
| `DELETE /containers/{id}` | Force-remove **без** удаления volume. 204. | 404, 503 |
| `GET /containers/{id}/logs/stream` | SSE стрим до отключения клиента. | 404 |

### `CreateContainerIn` (worker конструирует из template + servers.resources)

```python
name: str                  # deterministic, e.g. "gh-<server_id>"
image: str                 # e.g. "itzg/minecraft-server:latest"
env: dict[str, str]
ports: list[PortBinding]   # [{containerPort, hostPort, protocol="tcp"|"udp"}]
volumes: list[VolumeMount] # [{name, mountPath, readOnly}]
resources: Resources       # {cpuCores: float, memMb: int}
network: str | None        # default — bridge
read_only_root: bool       # default true
```

### Security defaults, всегда применяемые в `DockerFacade.create_and_start`

- `cap_drop=["ALL"]`
- `security_opt=["no-new-privileges:true"]`
- `read_only=spec.read_only_root` (default true)
- `tmpfs={"/tmp": ""}` если `read_only_root`
- `nano_cpus = int(resources.cpuCores * 1e9)`
- `mem_limit = f"{resources.memMb}m"`
- `restart_policy={"Name":"unless-stopped"}`
- `labels = {"gamehost.managed":"true","gamehost.name":name}`
- `name=spec.name` (Docker сам бракует дубли через 409)

### `ContainerOut`

```python
id: str
name: str
status: Literal["pending","running","stopped","failed"]
image: str
created_at: datetime
```

`status` нормализуется из docker-state:
- `created` → `pending`
- `running` → `running`
- `restarting` → `running`
- `paused` → `running`
- `exited` (exit_code=0) → `stopped`
- `exited` (exit_code≠0) → `failed`
- `dead` → `failed`

### `ContainerStatsOut`

```python
cpu_percent: float
mem_usage_mb: float
mem_limit_mb: float
```

Вычисляются из одного снапшота `client.containers.get(id).stats(stream=False)`.

## `DockerFacade` (`docker_facade.py`)

Единственная точка касания SDK. Тесты мокают **этот класс**.

```python
class DockerFacade:
    def __init__(self, base_url: str | None = None) -> None:
        self._client = docker.DockerClient(base_url=base_url) if base_url else docker.from_env()

    async def create_and_start(self, spec: ContainerSpec) -> ContainerOut: ...
    async def start(self, container_id: str) -> None: ...
    async def stop(self, container_id: str, *, timeout_s: int = 30) -> None: ...
    async def restart(self, container_id: str) -> None: ...
    async def remove(self, container_id: str) -> None: ...
    async def inspect(self, container_id: str) -> ContainerOut: ...
    async def stats(self, container_id: str) -> ContainerStatsOut: ...
    async def stream_logs(self, container_id: str) -> AsyncIterator[str]: ...
```

`docker` SDK синхронный — каждый метод оборачивает блокирующий вызов в `await asyncio.to_thread(...)`. `stream_logs` — генератор, читающий `client.containers.get(id).logs(stream=True, follow=True, timestamps=False)` chunks, каждый chunk через `asyncio.to_thread(next, it)`.

`ContainerSpec` — внутренний dataclass, который применяет security defaults поверх `CreateContainerIn` до похода в Docker.

## `ContainerService` (`domain/containers.py`)

Координирует use cases, мапит docker exceptions на доменные:

| docker exception | domain |
|---|---|
| `docker.errors.NotFound` | `ContainerNotFound` (404) |
| `docker.errors.APIError` со status_code 409 | `ContainerNameTaken` (409) |
| `docker.errors.APIError` прочее | `DockerUnavailable` (503) |
| `requests.exceptions.ConnectionError` | `DockerUnavailable` (503) |

`stop`/`start` идемпотентны: если контейнер уже в нужном состоянии, no-op.
`remove` всегда `force=True`, volumes остаются.

## SSE-стрим логов

`StreamingResponse` с `media_type="text/event-stream"`, генератор:
- На каждую строчку из `DockerFacade.stream_logs(id)` шлёт `data: <line>\n\n`.
- Каждые 15 секунд idle — `: ping\n\n` (heartbeat).
- При `asyncio.CancelledError` (отвал клиента) — корректно закрывает iterator (через `it.close()`).
- 404 если контейнер не существует — отвечается до начала стрима, обычным RFC 7807.

## Auth middleware (`core/auth.py`)

```python
async def require_api_key(authorization: str | None = Header(default=None)) -> None:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, detail="missing_bearer")
    presented = authorization.split(" ", 1)[1].strip()
    expected = get_settings().api_key.get_secret_value()
    if not hmac.compare_digest(presented, expected):
        raise HTTPException(401, detail="invalid_api_key")
```

Подключается как dependency на router `/api/v1/containers`. `/healthz` без auth.

## Конфиг (`core/config.py`)

```
api_key: SecretStr                       # NODE_AGENT_API_KEY, обязательно
docker_host: str | None = None           # DOCKER_HOST; None → docker.from_env()
log_level: str = "INFO"
default_network: str | None = None       # DEFAULT_NETWORK для всех контейнеров (если задано)
listen_port: int = 8080                  # NODE_AGENT_PORT
```

## Dockerfile (`apps/node_agent/Dockerfile`)

- builder: `python:3.12-slim` + `uv sync --frozen --no-dev --package gamehost-node`
- runtime: `python:3.12-slim`, non-root user `app` в группе `docker` (gid обычно 999), чтобы иметь доступ к смонтированному `/var/run/docker.sock`
- `EXPOSE 8080`
- `CMD ["uvicorn","gamehost_node.main:app","--host","0.0.0.0","--port","8080"]`

Make-цель `build-node-agent`:
```
build-node-agent:
	docker build -f apps/node_agent/Dockerfile -t gamehost-node:dev .
```

## Тесты

### `tests/conftest.py`
- session-scope fixture `mock_facade` — `MagicMock(spec=DockerFacade)` со всеми методами как `AsyncMock`.
- `client` — `AsyncClient` против `app` с `app.dependency_overrides[get_docker_facade] = lambda: mock_facade`.
- `_api_key` autouse — выставляет `NODE_AGENT_API_KEY` в env перед `get_settings()`.

### `test_auth.py`
- bearer отсутствует → 401 missing_bearer
- bearer с неверным ключом → 401 invalid_api_key
- bearer с правильным → ручка возвращает свой статус (e.g. 200 на `/api/v1/containers/<id>` mocked)
- `/healthz` без bearer → 200

### `test_containers_service_mock.py`
- create_and_start_returns_container_out
- create_with_existing_name_raises_container_name_taken (mock raises APIError 409)
- inspect_unknown_raises_container_not_found
- start_idempotent_when_running
- stop_idempotent_when_stopped
- delete_force_removes
- docker_connection_error_raises_docker_unavailable

### `test_containers_routes_mock.py`
- POST /containers happy-path → 201, camel response
- POST с неверным payload (mem<=0, неправильный protocol) → 422
- GET /containers/{id} → 200 со status и stats
- GET /containers/unknown → 404 container_not_found (RFC 7807)
- POST /containers/{id}/start|stop|restart → 200
- DELETE /containers/{id} → 204; повторно → 404
- POST с docker.errors.APIError 409 → 409 container_name_taken
- ConnectionError → 503 docker_unavailable
- SSE: GET /logs/stream возвращает text/event-stream и chunk'ы из mock'а

### `test_docker_real.py` под `@pytest.mark.docker_real`
- Skip по умолчанию (через `pyproject.toml` markers + addopts).
- Запускается `pytest -m docker_real`.
- Один тест: создаёт `busybox:latest` с командой `["sh","-c","echo hello && sleep 60"]`, проверяет `status=running`, читает первый chunk логов, stop, delete.

### Coverage
- `--cov=apps/node_agent/src/gamehost_node --cov-fail-under=70` локально + CI (через корневой pyproject — добавим второй source).

## Конфигурация workspace

`pyproject.toml` (root):
- `[tool.uv.workspace] members = ["apps/api", "apps/node_agent", "packages/shared"]`
- `mypy_path += "apps/node_agent"`
- `[tool.pytest.ini_options]` — `testpaths += "apps/node_agent/tests"`, `markers = ["docker_real: ..."]`
- `addopts` дополняется флагом `-m "not docker_real"` чтобы по умолчанию не запускать живой Docker

`packages/shared/pyproject.toml` остаётся без рантайм-зависимостей; `CamelModel` принесёт `pydantic` как зависимость shared.

## Definition of Done

- [ ] `apps/node_agent/` — uv workspace member, билдится отдельно.
- [ ] Все 8 эндпоинтов работают согласно таблице.
- [ ] Bearer middleware: 401 без/с неверным, 200 c правильным, healthz без auth.
- [ ] Контейнеры создаются с `cap_drop=ALL`, `no-new-privileges`, лимитами CPU/mem, security defaults применяются всегда.
- [ ] SSE стрим логов корректно отдаёт `text/event-stream` и закрывается при disconnect.
- [ ] Ошибки в формате `application/problem+json`.
- [ ] mock-тесты зелёные локально и в CI; `pytest -m docker_real` зелёный локально.
- [ ] `make lint typecheck test` зелёные глобально (workspace).
- [ ] Coverage ≥ 70% по `apps/node_agent`; общая coverage не падает ниже 70%.
- [ ] README получает раздел «Stage 3: node-agent» с примером деплоя через docker run и монтированием сокета.
- [ ] `CamelModel` живёт в `packages/shared`, дубликата в `apps/api` нет (или есть thin re-export).

## Verification вручную

1. `make build-node-agent` → image готов.
2. `docker run -d --name gh-node -p 8080:8080 -v /var/run/docker.sock:/var/run/docker.sock -e NODE_AGENT_API_KEY=test123 gamehost-node:dev`.
3. `curl localhost:8080/healthz` → 200.
4. `curl -X POST localhost:8080/api/v1/containers -H 'authorization: Bearer test123' -H 'content-type: application/json' -d '{"name":"smoke","image":"busybox:latest","env":{},"ports":[],"volumes":[],"resources":{"cpuCores":0.5,"memMb":64}}'` → 201.
5. `curl localhost:8080/api/v1/containers/smoke -H 'authorization: Bearer test123'` → status `running` + stats.
6. `curl -N localhost:8080/api/v1/containers/smoke/logs/stream -H 'authorization: Bearer test123'` → SSE.
7. `curl -X DELETE ... -H 'authorization: Bearer test123'` → 204; следующий GET → 404.

## Что НЕ входит в Stage 3

- `POST /containers/{id}/exec` (бэкап через tar) — Stage 7.
- ARQ worker, ставящий задачи на агента — Stage 4.
- Heartbeat ноды → controller (`last_seen_at`) — Stage 4+.
- mTLS между worker и agent — отложено.
- Запуск node-agent в `docker-compose.yml` локально — Stage 4 (когда появится потребитель).
- Создание Docker network'ов — на Stage 4 worker создаст; агент принимает имя сети как параметр.

## Следующий шаг

После реализации Stage 3 и зелёного CI — отдельный цикл brainstorm → spec → plan для **Stage 4 (worker + lifecycle: ARQ jobs `provision_server`/`start`/`stop`/`restart`/`delete`, выбор ноды, e2e через node-agent)**.

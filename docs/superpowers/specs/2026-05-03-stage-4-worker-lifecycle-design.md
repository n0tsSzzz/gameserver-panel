# Stage 4 — Worker + base lifecycle

**Дата:** 2026-05-03
**Источник ТЗ:** `claude_code_prompt.md` (§3, §5, §6, §9 Stage 4)
**Предыдущий этап:** Stage 3 (node-agent) — PR #4, в `main`.

## Context

Этапы 1–3 закончены. У нас есть auth (Stage 1), templates+nodes (Stage 2), node-agent (Stage 3) — всё умеет работать, но не связано в полный цикл провижининга. Stage 4 даёт пользователю команду «создать сервер» от и до: api ставит задачу в ARQ, worker выбирает наименее загруженную ноду, идёт в node-agent и поднимает Docker-контейнер, статус и IP/порт прописываются в БД. К концу Stage 4 GameHost становится минимально полезен — пользователь может реально подключиться к запущенному игровому серверу.

### Решённые ключевые развилки

- **Controller→agent auth (ответ A):** в `nodes.api_key` храним **plaintext** (миграция дропает `api_key_hash`). Admin всё ещё видит ключ один раз на POST /nodes; agent верифицирует ключ своим env'ом, controller читает plaintext из БД и подставляет в Bearer.
- **Scope:** lifecycle (provision/start/stop/restart/delete) + worker + servers/tasks/audit_log таблицы. Logs (tail+stream) → Stage 5. Members → Stage 6. Backups → Stage 7. audit_log создаётся минимально (таблица + базовые события); UI и полное покрытие — Stage 8.
- **Host port (ответ A):** worker задаёт `host_port=0`, Docker сам выбирает ephemeral; node-agent после inspect возвращает фактический порт; worker записывает в `servers.port`. Stop/start сохраняет порт; recreate меняет.
- **Уникальность server.name:** на пару `(owner_id, name)` — два разных пользователя могут иметь сервер с одним именем.

## Архитектура

```
                         api (FastAPI)
                              │ enqueue ARQ job; 202 + task_id
                              ▼
                          Redis (ARQ queue)
                              │
                              │ pop (worker)
                              ▼
                         worker (ARQ)
                              │ httpx Bearer api_key из nodes.api_key
                              ▼
                         node-agent /api/v1/containers/*
                              │
                              ▼
                          Docker daemon
```

## Структура каталогов (новое и изменения)

```
apps/api/
├── alembic/versions/0003_servers_tasks_audit.py
└── src/gamehost_api/
    ├── api/v1/
    │   ├── __init__.py             # +servers, +tasks routers
    │   ├── servers.py
    │   └── tasks.py
    ├── core/config.py              # +redis_url, +node_agent_timeout_s
    ├── db/models/
    │   ├── server.py
    │   ├── task.py
    │   └── audit_log.py
    ├── db/models/__init__.py       # +export Server, Task, AuditLog
    ├── domain/
    │   ├── exceptions.py           # +ServerNotFound, NoCapacity, InvalidServerState
    │   ├── servers.py              # ServerService
    │   └── node_selector.py        # least_loaded()
    ├── repositories/
    │   ├── servers.py
    │   ├── tasks.py
    │   └── audit_log.py
    ├── schemas/servers.py
    └── tasks/
        ├── __init__.py
        └── arq_pool.py             # ARQ pool singleton (lifespan-managed)

apps/worker/
├── pyproject.toml                  # gamehost-worker
├── Dockerfile
├── src/gamehost_worker/
│   ├── __init__.py
│   ├── main.py                     # WorkerSettings (ARQ entry)
│   ├── core/{config,logging}.py
│   ├── clients/node_agent_client.py
│   └── jobs/{__init__,_common,provision,start,stop,restart,delete}.py
└── tests/
    ├── conftest.py
    ├── test_node_selector.py
    └── test_jobs_e2e.py            # respx-mocked node-agent

packages/shared/src/gamehost_shared/enums.py   # +TaskStatus, TaskKind

deploy/docker-compose.yml           # +worker service
```

## Модель данных (миграция `0003_servers_tasks_audit`)

### `nodes` изменения
- DROP COLUMN `api_key_hash`
- ADD COLUMN `api_key text NOT NULL` (plaintext)

### `servers` (новая)

| column | type | constraints |
|---|---|---|
| `id` | UUID | PK, default `uuid4` |
| `owner_id` | UUID | NOT NULL FK→users.id ON DELETE CASCADE |
| `name` | text | NOT NULL, length 1..100 |
| `template_id` | UUID | NOT NULL FK→game_templates.id ON DELETE RESTRICT |
| `node_id` | UUID | NULL FK→nodes.id ON DELETE SET NULL |
| `container_id` | text | NULL |
| `status` | text | NOT NULL DEFAULT `'pending'`, CHECK `IN ('pending','provisioning','running','stopped','failed','deleting')` |
| `host` | text | NULL |
| `port` | integer | NULL |
| `env_overrides` | jsonb | NOT NULL DEFAULT `'{}'` |
| `resources` | jsonb | NOT NULL DEFAULT `'{}'` (`{cpuCores, memMb}`; пусто → `template.min_resources`) |
| `created_at` | timestamptz | NOT NULL DEFAULT now() |
| `updated_at` | timestamptz | NOT NULL DEFAULT now() |

Индексы: PK, UNIQUE `(owner_id, name)`, btree `(node_id, status)`, btree `(owner_id, created_at desc)`.

### `tasks` (новая)

| column | type | constraints |
|---|---|---|
| `id` | UUID | PK |
| `server_id` | UUID | NULL FK→servers.id ON DELETE SET NULL |
| `kind` | text | NOT NULL CHECK `IN ('provision','start','stop','restart','delete')` |
| `status` | text | NOT NULL DEFAULT `'pending'` CHECK `IN ('pending','running','succeeded','failed')` |
| `payload` | jsonb | NOT NULL DEFAULT `'{}'` |
| `error` | text | NULL |
| `started_at`/`finished_at` | timestamptz | NULL |
| `created_at` | timestamptz | NOT NULL DEFAULT now() |

Индексы: PK, btree `(server_id, created_at desc)`.

### `audit_log` (новая, минимально)

| column | type | constraints |
|---|---|---|
| `id` | bigserial | PK |
| `actor_id` | UUID | NULL FK→users.id ON DELETE SET NULL |
| `action` | text | NOT NULL |
| `target_type` | text | NOT NULL |
| `target_id` | text | NOT NULL |
| `meta` | jsonb | NOT NULL DEFAULT `'{}'` |
| `ip` | inet | NULL |
| `created_at` | timestamptz | NOT NULL DEFAULT now() |

Индекс: btree `(target_type, target_id, created_at desc)`. Append-only.

## Эндпоинты

Все требуют auth (`get_current_user`). Камелкейс, RFC 7807 ошибки. Async-операции возвращают 202.

| Метод/путь | Поведение | Ответ |
|---|---|---|
| `GET /api/v1/servers` | Список где `owner_id = current_user.id` (admin → все). Сорт `created_at desc`. | 200 `[ServerOut]` |
| `POST /api/v1/servers` | Body `ServerCreateIn`. Создаёт `servers (status=pending)` + `tasks (kind=provision, status=pending)`, ARQ.enqueue с `_job_id=task.id`. audit_log. | 202 `AcceptedOut` |
| `GET /api/v1/servers/{id}` | Owner или admin. | 200 `ServerOut`, 404 |
| `POST /api/v1/servers/{id}/start` | Admissible: status ∈ `{stopped, failed}`. Ставит task. | 202, 404, 409 |
| `POST /api/v1/servers/{id}/stop` | Admissible: status ∈ `{running}`. | 202, 404, 409 |
| `POST /api/v1/servers/{id}/restart` | Admissible: status ∈ `{running}`. | 202, 404, 409 |
| `PATCH /api/v1/servers/{id}` | `ServerPatchIn {envOverrides?, resources?}`. Только status=stopped. **Синхронно**, без ARQ. | 200 `ServerOut`, 409 invalid_state |
| `DELETE /api/v1/servers/{id}` | Set status=deleting, ставит delete-task. | 202 |
| `GET /api/v1/tasks/{id}` | Owner-сервера-связки или admin. | 200 `TaskOut`, 404 |

### DTOs (camelCase via `CamelModel`)

```python
class ServerCreateIn(CamelModel):
    name: str = Field(min_length=1, max_length=100)
    template_id: uuid.UUID
    env_overrides: dict[str, str] = Field(default_factory=dict)
    resources: Resources | None = None  # None → template.min_resources

class ServerPatchIn(CamelModel):
    env_overrides: dict[str, str] | None = None
    resources: Resources | None = None

class ServerOut(CamelModel):
    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    template_id: uuid.UUID
    node_id: uuid.UUID | None
    container_id: str | None
    status: ServerStatus
    host: str | None
    port: int | None
    env_overrides: dict[str, str]
    resources: dict[str, Any]
    created_at: datetime
    updated_at: datetime

class AcceptedOut(CamelModel):
    server_id: uuid.UUID
    task_id: uuid.UUID

class TaskOut(CamelModel):
    id: uuid.UUID
    server_id: uuid.UUID | None
    kind: TaskKind
    status: TaskStatus
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
```

`Resources` reused from `apps/node_agent/src/gamehost_node/schemas/containers.py` — лучше переехать в `packages/shared` как `gamehost_shared.resources` (минимальный рефактор).

### Authorization helper

```python
async def _authorize_server(server: Server, user: User) -> None:
    if server.owner_id != user.id and user.role != "admin":
        raise ServerNotFound(str(server.id))   # 404 чтобы не пробивать чужие id
```

## Worker

`WorkerSettings`:
```python
class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    functions = [provision, start, stop, restart, delete]
    on_startup = _on_startup     # creates engine, sessionmaker → ctx
    on_shutdown = _on_shutdown
    keep_result = 600
    max_jobs = 10
```

Все jobs принимают `task_id: str`. Каркас:

```python
async def provision(ctx, task_id_str: str) -> None:
    sm = ctx["sm"]
    task_id = uuid.UUID(task_id_str)
    async with sm() as s:
        await TasksRepo(s).mark_running(task_id)
        await s.commit()

    try:
        async with sm() as s:
            task = await TasksRepo(s).get(task_id)
            server = await ServersRepo(s).get(task.server_id)
            template = await TemplatesRepo(s).get(server.template_id)
            node = await least_loaded(s, server.resources)
            if node is None:
                raise NoCapacity()
            spec = build_create_spec(server, template)
            async with NodeAgentClient(node) as client:
                created = await client.create_container(spec)
                inspected = await client.get_container(created.id)
            host_port = _first_host_port(inspected)
            host = _host_from_endpoint(node.endpoint_url)
            await ServersRepo(s).set_provisioned(
                server.id, node_id=node.id, container_id=created.id,
                host=host, port=host_port, status="running"
            )
            await AuditRepo(s).record(
                action="server.provisioned", target_type="server", target_id=str(server.id),
                meta={"node_id": str(node.id), "container_id": created.id},
            )
            await s.commit()
        async with sm() as s:
            await TasksRepo(s).mark_succeeded(task_id)
            await s.commit()
    except Exception as exc:
        async with sm() as s:
            await ServersRepo(s).set_status(server.id, "failed")
            await TasksRepo(s).mark_failed(task_id, str(exc))
            await s.commit()
        raise
```

Аналогично `start`, `stop`, `restart`, `delete`.

### `NodeAgentClient`
- `httpx.AsyncClient` с base_url=`node.endpoint_url`, headers Bearer из `node.api_key`.
- timeout `node_agent_timeout_s` (default 10s).
- methods `create_container`, `get_container`, `start`, `stop`, `restart`, `delete`.
- Retry на `httpx.ConnectError` / `httpx.ReadTimeout`: 3 попытки, exponential 0.5s/1s/2s. На `httpx.HTTPStatusError` 5xx тоже retry; 4xx — сразу raise `NodeAgentHTTPError(status, body)`.
- 404 от агента → `domain.exceptions.ContainerMissing` (специальное чтобы worker мог обработать «контейнер уже исчез» в delete как success).

### Node selection (`domain/node_selector.py`)

```python
async def least_loaded(session, resources: dict[str, Any]) -> Node | None:
    req_cpu = float(resources.get("cpuCores", 1.0))
    req_mem = int(resources.get("memMb", 1024))
    stmt = text("""
        SELECT n.id,
               n.capacity_cpu - COALESCE(used.cpu, 0) AS free_cpu,
               n.capacity_mem_mb - COALESCE(used.mem, 0) AS free_mem,
               COALESCE(used.cpu, 0) / NULLIF(n.capacity_cpu::float, 0) AS load
        FROM nodes n
        LEFT JOIN (
            SELECT s.node_id,
                   SUM((s.resources->>'cpuCores')::float) AS cpu,
                   SUM((s.resources->>'memMb')::int) AS mem
            FROM servers s
            WHERE s.status IN ('provisioning','running')
            GROUP BY s.node_id
        ) used ON used.node_id = n.id
        WHERE n.status = 'online'
          AND (n.capacity_cpu - COALESCE(used.cpu, 0)) >= :req_cpu
          AND (n.capacity_mem_mb - COALESCE(used.mem, 0)) >= :req_mem
        ORDER BY load NULLS FIRST
        LIMIT 1
    """)
    row = (await session.execute(stmt, {"req_cpu": req_cpu, "req_mem": req_mem})).first()
    if row is None:
        return None
    return await session.get(Node, row.id)
```

### `arq_pool.py` (api side)

`get_arq_pool(app)` возвращает `arq.connections.ArqRedis` создаваемый в lifespan. `ServerService` использует его как `await pool.enqueue_job(kind, str(task.id), _job_id=str(task.id))`.

В тестах api-маршрутов `app.state.arq_pool` подменяется на `MagicMock(enqueue_job=AsyncMock(...))`.

## Конфигурация

### api `Settings` дополнения
```
redis_url: str = Field(default="redis://localhost:6379", alias="REDIS_URL")
node_agent_timeout_s: float = Field(default=10.0, alias="NODE_AGENT_TIMEOUT_S")
```

### worker `Settings` (новый файл)
```
database_url: str
redis_url: str = "redis://localhost:6379"
node_agent_timeout_s: float = 10.0
log_level: str = "INFO"
```

### `.env.example` дополнения
```
# Stage 4 — async worker
REDIS_URL=redis://localhost:6379
NODE_AGENT_TIMEOUT_S=10.0
```

## docker-compose

В `deploy/docker-compose.yml` добавляется сервис `worker`:
```yaml
worker:
  build:
    context: .
    dockerfile: apps/worker/Dockerfile
  environment:
    DATABASE_URL: postgresql+asyncpg://gamehost:gamehost@postgres:5432/gamehost
    REDIS_URL: redis://redis:6379
    NODE_AGENT_TIMEOUT_S: "10.0"
    LOG_LEVEL: INFO
  depends_on:
    postgres: { condition: service_healthy }
    redis: { condition: service_healthy }
  networks: [gamehost]
```

## Тесты

### worker
- `tests/conftest.py` — testcontainer Postgres, alembic upgrade, redis из docker-compose (или testcontainer Redis); `respx_mock` через `pytest-respx` для http-вызовов в node-agent; truncate per-test.
- `tests/test_node_selector.py` — 4 теста.
- `tests/test_jobs_e2e.py` — 6 тестов:
  - provision_succeeds_sets_running_with_port_and_host
  - provision_no_capacity_marks_failed
  - provision_node_agent_5xx_fails_after_retries
  - start_then_stop_e2e (full happy lifecycle)
  - delete_removes_server_row
  - delete_with_already_missing_container_succeeds (404 from agent treated as success)

### api new domain
- `apps/api/tests/test_servers_routes.py`:
  - get_unauth_returns_401
  - get_returns_only_my_servers
  - admin_sees_all
  - post_creates_pending_server_and_task_and_enqueues
  - post_with_invalid_template_id_returns_404
  - get_unknown_returns_404
  - patch_when_running_returns_409
  - patch_when_stopped_changes_env_and_resources
  - delete_sets_deleting_and_enqueues
  - access_to_other_users_server_returns_404
  - lifecycle_endpoints_require_correct_state (start running → 409, stop stopped → 409, etc.)
- `apps/api/tests/test_tasks_routes.py`:
  - get_my_task_returns_200
  - get_others_task_returns_404
  - get_admin_sees_any_task

### Coverage
- общая ≥70%
- domain/ ≥85%

## Definition of Done

- [ ] Миграция `0003` `up/down` чистые, тестовая БД на её основе работает.
- [ ] `nodes.api_key` plaintext; `api_key_hash` дропнут; admin POST /nodes возвращает `apiKey` ровно один раз; node-agent (Stage 3) продолжает работать.
- [ ] `/api/v1/servers/*` и `/api/v1/tasks/{id}` работают согласно таблице.
- [ ] worker запускается через `arq gamehost_worker.main.WorkerSettings`, подхватывает задачи из Redis.
- [ ] node-selection возвращает наименее загруженную ноду; при отсутствии capacity — `NoCapacity` → server.status=failed.
- [ ] e2e через respx: API → tasks-row → ARQ-pool mock → worker → respx-mock-agent → server.status=running с правильным host:port.
- [ ] PATCH вне stopped → 409.
- [ ] audit_log получает `server.created`/`server.start_requested`/`server.provisioned`/etc.
- [ ] `make lint typecheck test` зелёные; coverage ≥70% общая, ≥85% domain.
- [ ] README — секция Stage 4 + `make build-worker`.

## Verification вручную

1. `make migrate seed up` — postgres+redis+minio.
2. `cd apps/worker && uv run arq gamehost_worker.main.WorkerSettings` — worker крутится.
3. Запустить node-agent (Stage 3): `docker run -d -p 8080:8080 -v /var/run/docker.sock:/var/run/docker.sock -e NODE_AGENT_API_KEY=test123 gamehost-node:dev`.
4. Login admin → POST /api/v1/nodes c `endpointUrl=http://host.docker.internal:8080`, capacity. Получаем `apiKey`. Прописываем тот же ключ в env агента (если ещё нет).
5. POST /api/v1/servers `{name:"mc","templateId":<minecraft uuid>}` → 202 + taskId.
6. Polling GET /api/v1/tasks/{taskId} → succeeded; GET /api/v1/servers/{id} → status=running, host+port заполнены.
7. Подключиться minecraft-клиентом к `host:port`.

## Что НЕ входит в Stage 4

- Logs (`GET /servers/{id}/logs?tail=N` и `/logs/stream`) — Stage 5.
- Members (`server_members`) — Stage 6.
- Backups + S3 — Stage 7.
- Custom prometheus метрики — Stage 8.
- audit_log UI и широкое инструментирование — Stage 8.
- Heartbeat ноды → controller (`nodes.last_seen_at`) — отложено.

## Следующий шаг

После Stage 4 — Stage 5: logs (tail + SSE через Redis pub/sub).

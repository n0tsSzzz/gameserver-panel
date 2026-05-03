# GameHost

Платформа управления игровыми серверами (Minecraft, CS2, Valheim, …).
Это Этап 0 — скелет монорепо: uv workspace, tooling, локальная инфра-минимум, CI.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (менеджер зависимостей Python)
- Docker (рекомендуется [OrbStack](https://orbstack.dev/) на macOS)
- Python 3.12+

## Quick start

```bash
cp .env.example .env
make install        # uv sync + pre-commit install
make up             # postgres + redis + minio
make test           # запускает testcontainers Postgres
make down           # остановить инфру (volumes сохраняются)
```

## Make-цели

| Цель | Назначение |
|---|---|
| `install` | Установить зависимости и pre-commit хуки. |
| `lint` | `ruff check` + `ruff format --check`. |
| `format` | Авто-форматирование и автофиксы ruff. |
| `typecheck` | `mypy --strict apps packages`. |
| `test` | `pytest` (тесты автоматически поднимают Postgres через testcontainers). |
| `up` / `down` | Поднять/остановить локальную инфру (`deploy/docker-compose.yml`). |
| `migrate` / `revision` / `seed` | Заглушки до Этапов 1–2. |

## Структура

```
apps/
  api/         FastAPI публичное API (Stage 0: /healthz + /readyz)
  worker/      placeholder, Stage 4
  node_agent/  placeholder, Stage 3
packages/
  shared/      общие enum/contracts
deploy/
  docker-compose.yml   локальная инфра (postgres, redis, minio)
```

## Запуск API локально

```bash
make up
uv run uvicorn gamehost_api.main:app --reload --app-dir apps/api/src
curl localhost:8000/healthz   # {"status":"ok"}
curl localhost:8000/readyz    # {"status":"ready"} если Postgres доступен
```

## Stage 4: full server lifecycle

End-to-end provisioning loop is live. API ставит ARQ-задачу → worker
выбирает наименее загруженную ноду → зовёт node-agent → Docker запускает
контейнер. Сервер получает `host:port` для подключения.

```bash
# 1. инфра
make up && make migrate && make seed

# 2. node-agent (Stage 3) с ключом
docker run -d --name gh-node -p 8080:8080 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e NODE_AGENT_API_KEY=test-node-key gamehost-node:dev

# 3. worker
make build-worker
docker run -d --name gh-worker --network gamehost_gamehost \
  -e DATABASE_URL=postgresql+asyncpg://gamehost:gamehost@postgres:5432/gamehost \
  -e REDIS_URL=redis://redis:6379 gamehost-worker:dev
# или локально: cd apps/worker && uv run arq gamehost_worker.main.WorkerSettings

# 4. login admin → POST /api/v1/nodes c endpoint и тем же ключом
# 5. POST /api/v1/servers {"name":"mc","templateId":"<uuid>"} → 202 + taskId
# 6. polling GET /api/v1/tasks/{taskId} → succeeded
# 7. GET /api/v1/servers/{id} → status=running, host+port заполнены
```

`make build-worker` — сборка образа worker'а. Stage 4 включает миграцию `0003`,
которая меняет `nodes.api_key_hash` на plaintext `nodes.api_key` (controller
теперь умеет говорить с node-agent'ом, читая ключ из БД).

## Stage 3: node-agent

Отдельный сервис, живущий на каждой ноде. Принимает команды от worker'а
(будет на Stage 4) и проксирует в локальный Docker daemon.

```bash
# 1. собрать образ
make build-node-agent

# 2. запустить с монтированным docker.sock
docker run -d --name gh-node \
  -p 8080:8080 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e NODE_AGENT_API_KEY=test123 \
  gamehost-node:dev

# 3. проверить
curl localhost:8080/healthz
curl -X POST localhost:8080/api/v1/containers \
  -H 'authorization: Bearer test123' -H 'content-type: application/json' \
  -d '{"name":"smoke","image":"busybox:latest","resources":{"cpuCores":0.5,"memMb":64}}'
```

Все эндпоинты под `/api/v1/containers/*` требуют Bearer-токен (`NODE_AGENT_API_KEY`).
SSE-логи: `GET /api/v1/containers/{id}/logs/stream` (`text/event-stream`).
Контейнеры стартуют с `cap_drop=ALL`, `no-new-privileges`, лимитами CPU/mem,
`read_only` rootfs (опционально).

Real-Docker smoke-тест: `pytest -m docker_real apps/node_agent/tests` (требует
запущенный Docker daemon локально; в обычный `make test` не входит).

## Stage 2: templates & nodes (admin)

```bash
# 1. bootstrap admin (reads BOOTSTRAP_ADMIN_* from .env) + 5 canonical templates
make seed

# 2. login as admin
ACCESS=$(curl -s -X POST localhost:8000/api/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"email":"admin@gh.local","password":"change-me-in-prod"}' | jq -r .accessToken)

# 3. list templates (admin sees all, user sees only is_public=true)
curl localhost:8000/api/v1/templates -H "authorization: Bearer $ACCESS"

# 4. create node (apiKey is returned exactly once)
curl -X POST localhost:8000/api/v1/nodes \
  -H "authorization: Bearer $ACCESS" -H 'content-type: application/json' \
  -d '{"name":"node-1","endpointUrl":"http://node-1:8080","capacityCpu":"8.00","capacityMemMb":16384}'
```

`make seed` is idempotent: existing admins stay admins, existing templates are not overwritten by the seeder. Templates can be edited via `PATCH /api/v1/templates/{id}`; to retire one, set `isPublic=false`.

## Auth (Stage 1)

```bash
# 1. register
curl -X POST localhost:8000/api/v1/auth/register \
  -H 'content-type: application/json' \
  -d '{"email":"a@b.test","password":"hunter22hunter22"}'

# 2. login (saves refresh cookie to c.txt)
curl -c c.txt -X POST localhost:8000/api/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"email":"a@b.test","password":"hunter22hunter22"}'

# 3. authenticated request
ACCESS=$(curl -s -c c.txt -X POST localhost:8000/api/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"email":"a@b.test","password":"hunter22hunter22"}' | jq -r .accessToken)
curl localhost:8000/api/v1/auth/me -H "authorization: Bearer $ACCESS"

# 4. rotate refresh
curl -b c.txt -c c.txt -X POST localhost:8000/api/v1/auth/refresh

# 5. logout
curl -b c.txt -X POST localhost:8000/api/v1/auth/logout
```

Errors are RFC 7807 (`application/problem+json`), e.g. `{"type":"about:blank","title":"Invalid credentials","status":401,"code":"invalid_credentials"}`.

OpenAPI — `make openapi` записывает спецификацию в `docs/openapi.json`.

## Этапы реализации

См. `claude_code_prompt.md` (§9). Spec и план Stage 1 — в `docs/superpowers/`.
После Stage 1 — отдельный цикл brainstorm для Stage 2 (templates + nodes admin).

## Troubleshooting

- **`make test` падает с `docker: command not found`** — нужен запущенный Docker daemon (OrbStack/Docker Desktop). Testcontainers поднимает Postgres через него.
- **Порт 5432 занят** — измени `POSTGRES_PORT` в `.env`.
- **`uv sync` ругается на Python 3.14** — workspace требует `>=3.12`, локальный 3.14 совместим. Если uv не находит интерпретатор, используй `uv python install 3.12`.

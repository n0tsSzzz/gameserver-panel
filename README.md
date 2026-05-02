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

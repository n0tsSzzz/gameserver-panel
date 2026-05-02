# Stage 1 — Auth + users

**Дата:** 2026-05-02
**Источник ТЗ:** `claude_code_prompt.md` (§5, §6, §7, §9 Stage 1, §10)
**Предыдущий этап:** Stage 0 (monorepo skeleton) — см. PR #1, в `main`.

## Context

После Этапа 0 у нас рабочий скелет: uv workspace, FastAPI с `/healthz`/`/readyz`, Postgres+Redis+MinIO в compose, CI зелёный. На Этапе 1 строим первый «настоящий» домен — аутентификацию и сущность пользователя — и одновременно ставим слойный каркас (`api/v1`, `domain`, `repositories`, `db`, `schemas`, `core`), на который будут «приземляться» все следующие домены (templates, nodes, servers, …). Цель — закрыть auth-флоу полностью (register/login/refresh/logout/me) с миграциями, RFC 7807 ошибками, structlog'ом и `/metrics`, чтобы каждый последующий этап только добавлял свои роутеры и use cases.

### Решённые ключевые развилки

- **PK таблиц:** UUIDv4 через Python (`uuid.uuid4`), без extension'ов и зависимостей. K UUIDv7 можно мигрировать позже без переписывания данных.
- **Объём этапа (ответ B):** auth-флоу + слойный каркас + Alembic init + structlog (с redact-фильтром секретов) + `/metrics` через prometheus-fastapi-instrumentator. Rate-limit `/auth/*` и custom-метрики — отложены.
- **Logout (ответ A):** отзываем только текущий refresh; «выйти со всех устройств» в MVP не нужен.
- **JWT:** HS256 (моно-issuer/verifier).
- **Refresh:** opaque random (`secrets.token_urlsafe(32)`), хранится как `sha256(token)` в БД, **не JWT**. Ротация на `/refresh`; reuse revoked-токена → revoke всех refresh пользователя.

## Структура каталогов

```
apps/api/
├── alembic.ini
├── alembic/
│   ├── env.py                              # async, читает DATABASE_URL из Settings
│   ├── script.py.mako
│   └── versions/0001_users_refresh_tokens.py
├── src/gamehost_api/
│   ├── main.py                             # include_router, exception handlers, instrumentator, logging init
│   ├── core/
│   │   ├── config.py                       # +SECRET_KEY, TTL, cookie_secure, argon2 params
│   │   ├── security.py                     # hash_password, verify_password, create_access_jwt, decode_access_jwt, generate_refresh_token, hash_refresh_token
│   │   ├── logging.py                      # structlog config + redact-processor
│   │   └── errors.py                       # ProblemDetails (RFC 7807), DomainError handler, ValidationError handler
│   ├── api/v1/
│   │   ├── __init__.py                     # router = APIRouter(prefix="/api/v1")
│   │   ├── deps.py                         # get_session, get_current_user, get_uow, get_request_id
│   │   └── auth.py                         # POST /auth/register|login|refresh|logout, GET /auth/me
│   ├── db/
│   │   ├── base.py                         # DeclarativeBase + типизированные mixin'ы (TimestampMixin)
│   │   ├── session.py                      # async engine + sessionmaker, get_session()
│   │   └── models/{user,refresh_token}.py
│   ├── domain/
│   │   ├── exceptions.py                   # DomainError, InvalidCredentials, EmailAlreadyTaken, RefreshInvalid, UserInactive
│   │   └── auth.py                         # AuthService (register/login/refresh/logout/get_me)
│   ├── repositories/{users,refresh_tokens}.py
│   ├── schemas/
│   │   ├── base.py                         # CamelModel (alias_generator=to_camel, populate_by_name=True)
│   │   └── auth.py                         # RegisterIn, LoginIn, AccessTokenOut, MeOut
│   └── scripts/export_openapi.py           # для make openapi
└── tests/
    ├── conftest.py                         # testcontainers Postgres + alembic upgrade head, AsyncClient, clean_db
    ├── factories.py                        # UserFactory с заранее посчитанным argon2-хешем
    ├── test_health.py                      # без изменений
    └── test_auth.py
```

## Модель данных

### `users`
- `id` UUID PK (default `uuid.uuid4` в SQLAlchemy)
- `email` text NOT NULL — нормализуется (`lower().strip()`) в коде; **unique index по `lower(email)`**
- `password_hash` text NOT NULL (argon2id)
- `role` text NOT NULL DEFAULT `'user'` + CHECK (`role IN ('user','admin')`). Postgres ENUM не используем — в alembic'e он болезненный. В Python — `gamehost_shared.UserRole(StrEnum)`.
- `is_active` bool NOT NULL DEFAULT true
- `created_at` timestamptz NOT NULL DEFAULT now()

### `refresh_tokens`
- `id` UUID PK
- `user_id` UUID NOT NULL FK → `users.id` ON DELETE CASCADE
- `token_hash` text NOT NULL UNIQUE — `sha256(opaque_token).hex()`
- `expires_at` timestamptz NOT NULL
- `revoked_at` timestamptz NULL
- `user_agent` text NULL
- `ip` inet NULL
- `created_at` timestamptz NOT NULL DEFAULT now()
- индексы: PK, btree `(user_id, revoked_at)`, unique `token_hash`

## Эндпоинты `/api/v1/auth/*`

Все ответы JSON, ошибки — `application/problem+json` (RFC 7807). Поля JSON-тел — camelCase через alias.

| Метод/путь | Запрос | Поведение | Ответ |
|---|---|---|---|
| `POST /register` | `{email, password}` (минимум 8 символов) | Нормализует email, проверяет уникальность по `lower(email)`, argon2-хеш, инсерт. **Не логинит**. | 201 + `MeOut`. На дубликат — 409 `email_taken`. |
| `POST /login` | `{email, password}` | argon2-verify; ошибка/нет пользователя/inactive → единый `401 invalid_credentials` (anti-enumeration). На успех — создаёт refresh-row + access. | 200 `{accessToken, tokenType:"bearer"}` + `Set-Cookie: gh_refresh=...; HttpOnly; Secure; SameSite=Lax; Path=/api/v1/auth`. |
| `POST /refresh` | cookie `gh_refresh` | Находит row по `sha256(token)`. Если нет / revoked / expired → 401 (`refresh_invalid`); если revoked-reuse — дополнительно revoke все refresh этого `user_id`. На успех — старый row `revoked_at=now()`, новый row + новый access. | 200 `{accessToken, tokenType}` + новый `Set-Cookie`. |
| `POST /logout` | cookie `gh_refresh` (опц.) | Помечает row `revoked_at=now()` если найден. Очищает cookie. | 204. |
| `GET /me` | `Authorization: Bearer <access>` | Декодирует JWT (HS256), проверяет `exp`, грузит user. | 200 `MeOut {id, email, role, isActive, createdAt}`. 401 если невалид/просрочен. |

## Безопасность

- **Пароли:** argon2id через `argon2-cffi`, дефолтные параметры библиотеки (memory_cost=65536, time_cost=3, parallelism=4); параметры в `Settings`.
- **JWT:** HS256, `SECRET_KEY` (`SecretStr`, ≥32 байт). Claims access: `sub`, `email`, `role`, `iat`, `exp`, `type:"access"`. `python-jose[cryptography]`.
- **Refresh:** opaque `secrets.token_urlsafe(32)`, в БД — `sha256(token).hex()`. TTL 30 дней. Cookie `gh_refresh`, `Path=/api/v1/auth`, `HttpOnly`, `Secure` (управляется `cookie_secure: bool`), `SameSite=Lax`.
- **Anti-enumeration:** `/login` отвечает идентично для unknown/wrong-password/inactive.
- **Refresh reuse defence:** при попытке использовать revoked токен — revoke всех refresh пользователя.
- **Логи:** structlog redact-processor зануляет ключи `password|token|secret|authorization|cookie|set-cookie|gh_refresh` (case-insensitive).
- **`/metrics`:** `prometheus-fastapi-instrumentator` с дефолтными метриками HTTP.

## Конфигурация

`core/config.py` дополнения:
```
secret_key: SecretStr            # обязательное; в .env
access_token_ttl_seconds: int = 900
refresh_token_ttl_seconds: int = 2592000  # 30 дней
cookie_secure: bool = True        # dev: false
cookie_domain: str | None = None
argon2_memory_cost: int = 65536
argon2_time_cost: int = 3
argon2_parallelism: int = 4
log_level: str = "INFO"
```

`.env.example` пополняется новыми ключами с безопасными дефолтами для dev (`cookie_secure=false`, dev-`SECRET_KEY` помечен «replace in prod»).

## Alembic

- `alembic init -t async alembic` внутри `apps/api/`.
- `env.py` импортирует `Base.metadata` из `gamehost_api.db.base` и URL из `Settings()`.
- Первая миграция `0001_users_refresh_tokens` написана вручную (не autogenerate, чтобы выровнять CHECK-constraint и unique-index по `lower(email)` сразу), но проверена через `alembic upgrade head` → `downgrade base`.
- Make-цели:
  - `make migrate` → `uv run --package gamehost-api alembic -c apps/api/alembic.ini upgrade head`
  - `make revision m="..."` → `... alembic ... revision --autogenerate -m "..."`

## Логирование

`core/logging.py` конфигурирует structlog один раз при старте (lifespan):
- processors: `merge_contextvars`, `add_log_level`, `TimeStamper(fmt="iso")`, `add_logger_name`, `redact_secrets`, `format_exc_info`, `JSONRenderer()`.
- redact-процессор обходит `event_dict` (плюс вложенные dict/list) и заменяет значения чувствительных ключей на `"***"`.

Middleware `request_id`: на каждый запрос — `uuid4()` в contextvar; добавляется в response-header `X-Request-ID`. structlog `merge_contextvars` подхватит автоматически.

## Тесты

- `tests/conftest.py` (расширение Stage 0):
  - `postgres_url` (session-scope) — testcontainer Postgres 16 alpine с asyncpg-driver.
  - `_apply_migrations` (session-scope, autouse) — `alembic upgrade head` после старта контейнера.
  - `clean_db` (function-scope, autouse) — `TRUNCATE users, refresh_tokens RESTART IDENTITY CASCADE` между тестами.
  - `client` — `AsyncClient` против `app` через `ASGITransport`, lifespan активирован.
  - `secret_key` фикстура — фиксированный 32-байтный ключ через `monkeypatch.setenv`.
- `tests/factories.py`:
  - `make_user(email, password=DEFAULT, role="user", is_active=True)` — пишет в БД через сессию, использует кешированный argon2-хеш для дефолтного пароля.
- `tests/test_auth.py` (минимум 12 тестов):
  - register: success; duplicate → 409; bad email → 422; short password → 422.
  - login: success (access + cookie); wrong password → 401 invalid_credentials; unknown email → 401 invalid_credentials (одинаковый ответ); inactive user → 401.
  - refresh: success + старый токен 401 после ротации; missing cookie → 401; expired refresh → 401; reuse revoked → 401 + все остальные refresh user'а revoked.
  - logout: с cookie → 204 + повторный refresh 401; без cookie → 204.
  - me: with access → 200 + правильные поля; без → 401; expired access → 401.

Покрытие: `pytest-cov` в dev-deps, `--cov=apps/api/src/gamehost_api --cov-fail-under=70` в CI.

## Критические файлы (создать или изменить)

**Создать:**
- `apps/api/alembic.ini`, `apps/api/alembic/env.py`, `apps/api/alembic/script.py.mako`
- `apps/api/alembic/versions/0001_users_refresh_tokens.py`
- `apps/api/src/gamehost_api/core/{security,logging,errors}.py`
- `apps/api/src/gamehost_api/api/__init__.py`, `api/v1/{__init__,deps,auth}.py`
- `apps/api/src/gamehost_api/db/{base,session}.py`, `db/models/{__init__,user,refresh_token}.py`
- `apps/api/src/gamehost_api/domain/{__init__,exceptions,auth}.py`
- `apps/api/src/gamehost_api/repositories/{__init__,users,refresh_tokens}.py`
- `apps/api/src/gamehost_api/schemas/{__init__,base,auth}.py`
- `apps/api/src/gamehost_api/scripts/export_openapi.py`
- `apps/api/tests/factories.py`, `apps/api/tests/test_auth.py`

**Изменить:**
- `apps/api/pyproject.toml` — добавить: `argon2-cffi>=23,<25`, `python-jose[cryptography]>=3.3,<4`, `structlog>=24,<26`, `prometheus-fastapi-instrumentator>=7,<8`, `alembic>=1.13,<2`. Dev: `pytest-cov>=5,<7`, `polyfactory>=2.18,<3`.
- `apps/api/src/gamehost_api/main.py` — include router, register exception handlers, init logging, request_id middleware, instrumentator.
- `apps/api/src/gamehost_api/core/config.py` — добавить новые поля Settings.
- `apps/api/tests/conftest.py` — миграции + clean_db.
- `Makefile` — заменить заглушки `migrate`/`revision` на реальные команды; добавить `openapi`.
- `.env.example` — новые ключи.
- `README.md` — раздел «Auth» с примерами `curl`.

**Менять размер модели `gamehost_shared`:**
- Добавить `UserRole(StrEnum)` в `packages/shared/src/gamehost_shared/enums.py`.

## Definition of Done

- [ ] `make migrate` накатывает миграцию `0001_users_refresh_tokens` на пустую БД и `downgrade base` корректно откатывает.
- [ ] Все 5 эндпоинтов `/api/v1/auth/*` работают согласно описанному поведению.
- [ ] Ошибки в формате `application/problem+json` для доменных и валидационных случаев.
- [ ] Refresh ротируется при `/refresh`; reuse revoked → revoke всех refresh пользователя.
- [ ] Логи в JSON через structlog; пароли/токены/cookie/authorization редактятся; `request_id` присутствует в каждой строке и в заголовке `X-Request-ID`.
- [ ] `/metrics` отдаёт prometheus-формат.
- [ ] `make lint`, `make typecheck`, `make test` зелёные локально и в CI.
- [ ] Покрытие ≥ 70% общее, ≥ 85% по `domain/`.
- [ ] `.env.example` содержит все новые ключи.
- [ ] README получает раздел «Auth» с примерами `curl register/login/me/refresh/logout`.

## Verification (вручную)

1. `cp .env.example .env && make up && make migrate`.
2. `uv run --package gamehost-api uvicorn gamehost_api.main:app --reload --app-dir apps/api/src`.
3. `curl -X POST localhost:8000/api/v1/auth/register -H 'content-type: application/json' -d '{"email":"a@b.test","password":"hunter22hunter22"}'` → 201 `MeOut`.
4. `curl -c c.txt -X POST .../auth/login -H 'content-type: application/json' -d '{"email":"a@b.test","password":"hunter22hunter22"}'` → 200, `accessToken` в теле, `gh_refresh` в `c.txt`.
5. `curl localhost:8000/api/v1/auth/me -H "authorization: bearer <access>"` → 200 + профиль.
6. `curl -b c.txt -c c.txt -X POST .../auth/refresh` → 200, новый access, cookie ротирована; повторный refresh со старой cookie → 401.
7. `curl -b c.txt -X POST .../auth/logout` → 204; следующий refresh с этой cookie → 401.
8. `curl localhost:8000/metrics` — prometheus-формат.

## Что НЕ входит в Этап 1

- Email verification, password reset, "logout all devices".
- Rate-limit на `/auth/*` (отдельная задача либо к моменту прод-выкладки).
- Custom prometheus-метрики (`gh_servers_total`, `gh_task_duration_seconds`, …) — Этап 8.
- OpenTelemetry трейсинг.
- Admin-endpoints для управления пользователями (в ТЗ MVP их нет; на Этапе 2 пойдут templates/nodes admin CRUD).

## Следующий шаг

После реализации Этапа 1 и зелёного CI — отдельный цикл brainstorm → spec → plan для **Этапа 2 (templates + nodes admin CRUD + сидер шаблонов игр)**.

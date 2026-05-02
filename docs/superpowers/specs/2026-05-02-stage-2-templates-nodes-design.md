# Stage 2 — Templates + Nodes (admin) + bootstrap admin

**Дата:** 2026-05-02
**Источник ТЗ:** `claude_code_prompt.md` (§5, §6, §9 Stage 2, §10)
**Предыдущий этап:** Stage 1 (auth + users) — PR #2, в `main`.

## Context

После Stage 1 у нас рабочий auth-флоу и слойный каркас (`api/v1`, `domain`, `repositories`, `schemas`, `db`, `core`). Stage 2 добавляет два администрируемых домена — **game templates** (каталог канонических игр) и **nodes** (физические/логические ноды для будущего размещения серверов) — плюс инфраструктуру для bootstrap первого админа. С этим админ может зайти, заведенные ноды и темплейты заскорят в БД, и Stage 4 (worker + lifecycle) сможет выбирать ноду и стартовать контейнер из шаблона.

### Решённые ключевые развилки

- **Bootstrap admin (ответ A):** идемпотентный сидер `seed_admin.py` читает `BOOTSTRAP_ADMIN_EMAIL` + `BOOTSTRAP_ADMIN_PASSWORD` из env и создаёт/апгрейдит пользователя до `admin`. Запускается через `make seed` на старте окружения.
- **DELETE на templates:** не делаем. Гасим неактуальный темплейт через `is_public=false`. ТЗ §6 описывает только GET/POST/PATCH.
- **DELETE на nodes:** делаем (в ТЗ §6). Hard delete; на Stage 2 серверов ещё нет, на Stage 4 добавится защитная проверка.
- **role/status enums:** text + CHECK constraint, не Postgres ENUM (тот же выбор, что в Stage 1 для `users.role`).
- **API ключ ноды:** возвращается plaintext ровно один раз в ответе на `POST /nodes`; в БД хранится `argon2id(api_key)`.

## Структура каталогов (новое и изменения)

```
apps/api/
├── alembic/versions/0002_templates_nodes.py
├── src/gamehost_api/
│   ├── api/v1/
│   │   ├── __init__.py            # +include_router(templates), +include_router(nodes)
│   │   ├── deps.py                # +require_admin
│   │   ├── templates.py
│   │   └── nodes.py
│   ├── core/
│   │   └── config.py              # +bootstrap_admin_email, bootstrap_admin_password
│   ├── db/models/
│   │   ├── __init__.py            # +export GameTemplate, Node
│   │   ├── game_template.py
│   │   └── node.py
│   ├── domain/
│   │   ├── exceptions.py          # +TemplateNotFound, NodeNotFound, SlugAlreadyTaken, NodeNameTaken, Forbidden
│   │   ├── templates.py
│   │   └── nodes.py
│   ├── repositories/
│   │   ├── templates.py
│   │   └── nodes.py
│   ├── schemas/
│   │   ├── templates.py
│   │   └── nodes.py
│   └── scripts/
│       ├── seed_admin.py
│       └── seed_templates.py
└── tests/
    ├── factories.py               # +make_admin, make_template, make_node
    ├── test_templates_service.py
    ├── test_templates_routes.py
    ├── test_nodes_service.py
    ├── test_nodes_routes.py
    └── test_seed_scripts.py
```

## Модель данных

### `game_templates`

| column | type | constraints |
|---|---|---|
| `id` | UUID | PK, default `uuid4` |
| `slug` | text | NOT NULL UNIQUE |
| `display_name` | text | NOT NULL |
| `docker_image` | text | NOT NULL |
| `default_env` | jsonb | NOT NULL DEFAULT `'{}'` |
| `default_ports` | jsonb | NOT NULL DEFAULT `'[]'` (массив `{"container":int,"protocol":"tcp\|udp"}`) |
| `default_volumes` | jsonb | NOT NULL DEFAULT `'[]'` |
| `min_resources` | jsonb | NOT NULL DEFAULT `'{}'` (например `{"cpu":1.0,"memMb":1024}`) |
| `is_public` | bool | NOT NULL DEFAULT true |
| `created_at` | timestamptz | NOT NULL DEFAULT now() |
| `updated_at` | timestamptz | NOT NULL DEFAULT now() (обновляется в `repository.update`) |

Индекс: PK + UNIQUE на `slug`.

### `nodes`

| column | type | constraints |
|---|---|---|
| `id` | UUID | PK |
| `name` | text | NOT NULL UNIQUE |
| `endpoint_url` | text | NOT NULL |
| `api_key_hash` | text | NOT NULL (argon2id) |
| `capacity_cpu` | numeric(5,2) | NOT NULL |
| `capacity_mem_mb` | integer | NOT NULL |
| `status` | text | NOT NULL DEFAULT `'online'` + CHECK `IN ('online','offline','drain')` |
| `last_seen_at` | timestamptz | NULL (заполняется на Stage 4+ heartbeat'ом) |
| `created_at` | timestamptz | NOT NULL DEFAULT now() |

Индекс: PK + UNIQUE на `name`.

### Миграция `0002_templates_nodes`

Hand-written (не autogenerate). `upgrade` создаёт обе таблицы со всеми CHECK и индексами; `downgrade` дропает обе таблицы (порядок обратный созданию). Перед миграцией нужен Stage 1 (`0001`) — alembic это контролирует.

## Эндпоинты

Все ответы JSON; ошибки RFC 7807 (`application/problem+json`); поля camelCase через `CamelModel`. Префикс `/api/v1`.

### `templates.py`

| Метод/путь | Авторизация | Поведение | Ответ |
|---|---|---|---|
| `GET /templates` | любой авторизованный | Если user — фильтр `is_public=true`; admin — все. Сорт по `display_name`. | 200 `[TemplateOut, …]` |
| `POST /templates` | admin | Body `TemplateCreateIn`. Дубликат `slug` → `SlugAlreadyTaken` (409). | 201 `TemplateOut` |
| `PATCH /templates/{id}` | admin | Body `TemplatePatchIn` (все опциональные). 404 → `TemplateNotFound`. | 200 `TemplateOut` |

`TemplateCreateIn`: `slug, displayName, dockerImage, defaultEnv, defaultPorts, defaultVolumes, minResources, isPublic`. Валидация: `slug` matches `^[a-z0-9][a-z0-9-]{0,63}$`.
`TemplatePatchIn`: те же поля, все `Optional`.
`TemplateOut`: + `id, createdAt, updatedAt`.

### `nodes.py`

| Метод/путь | Авторизация | Поведение | Ответ |
|---|---|---|---|
| `GET /nodes` | admin | Список всех нод. | 200 `[NodeOut, …]` |
| `POST /nodes` | admin | Body `NodeCreateIn`. Сервис генерирует `secrets.token_urlsafe(32)`, argon2-хешит, кладёт в `api_key_hash`. Plaintext возвращается один раз в `apiKey`. Дубликат `name` → `NodeNameTaken` (409). | 201 `NodeCreateOut` (= `NodeOut + apiKey: str`) |
| `PATCH /nodes/{id}` | admin | Body `NodePatchIn`: `endpointUrl?, capacityCpu?, capacityMemMb?, status?` где `status ∈ {online, drain}`. Валидатор отбивает `offline` (это автоматический статус, ставится heartbeat'ом). 404 → `NodeNotFound`. | 200 `NodeOut` |
| `DELETE /nodes/{id}` | admin | Hard delete. На Stage 4+ добавим проверку «нет активных серверов». | 204 |

`NodeCreateIn`: `name, endpointUrl, capacityCpu, capacityMemMb`.
`NodeOut`: `id, name, endpointUrl, capacityCpu, capacityMemMb, status, lastSeenAt, createdAt`. **Без** `api_key_hash`.

### Dependency `require_admin`

В `api/v1/deps.py`:

```python
async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise Forbidden()
    return user
```

`Forbidden` — новая `DomainError(status_code=403, code="forbidden")`. Для admin-only роутеров вешаем `dependencies=[Depends(require_admin)]` на router-level, чтобы не повторять в каждом эндпоинте.

`GET /templates` использует `get_current_user` (любая роль) и в use case различает поведение admin vs user.

## Сервисы (use cases)

### `TemplateService`

```
list(actor: User) -> list[GameTemplate]   # admin → все, user → is_public=true
create(payload) -> GameTemplate           # raises SlugAlreadyTaken
update(id, payload) -> GameTemplate       # raises TemplateNotFound
```

### `NodeService`

```
list() -> list[Node]
create(payload) -> tuple[Node, plaintext_api_key]  # generate→hash→store; raises NodeNameTaken
update(id, payload) -> Node                # status restricted to online/drain on this layer; raises NodeNotFound
delete(id) -> None                         # raises NodeNotFound
verify_api_key(plain, node) -> bool        # used by node-agent / worker on Stage 3+, lives in domain/nodes.py for reuse
```

API-ключ: `secrets.token_urlsafe(32)`; `argon2id` хеш через `core.security.hash_password` (тот же hasher, что и для пользовательских паролей — параметры из Settings, разделять не нужно: argon2 устойчив к одинаковому профилю на разных сущностях).

## Конфигурация (`core/config.py`)

Добавляются поля:

```
bootstrap_admin_email: str | None = Field(default=None, alias="BOOTSTRAP_ADMIN_EMAIL")
bootstrap_admin_password: SecretStr | None = Field(default=None, alias="BOOTSTRAP_ADMIN_PASSWORD")
```

Опциональные на уровне Settings (тесты живут без них). Сидер `seed_admin.py` сам падает с понятным сообщением, если они не заданы.

`.env.example` пополняется:

```
BOOTSTRAP_ADMIN_EMAIL=admin@gh.local
BOOTSTRAP_ADMIN_PASSWORD=change-me-in-prod
```

## Сидеры

### `scripts/seed_admin.py`

Алгоритм:
1. Читает `BOOTSTRAP_ADMIN_EMAIL`/`BOOTSTRAP_ADMIN_PASSWORD` из `Settings()`. Если хоть одно `None` — print error, sys.exit(1).
2. Открывает `make_engine`/`make_sessionmaker`, через `UserRepository.get_by_email`.
3. Если нет — создаёт с `role='admin'`, паролем-аргументом, `is_active=True`.
4. Если есть и `role='user'` — обновляет `role='admin'`. Если уже admin — no-op.
5. structlog `info` действия (`created_admin` / `promoted_to_admin` / `already_admin`).

### `scripts/seed_templates.py`

Алгоритм: для каждого записи в `_TEMPLATES` (литеральный список ниже) делает `INSERT ... ON CONFLICT (slug) DO NOTHING` через core SQLAlchemy. Не апдейтит существующие — админ может править через PATCH, сидер не должен затирать ручные правки.

`_TEMPLATES`:

| slug | display_name | docker_image | default_ports | min_resources |
|---|---|---|---|---|
| `minecraft-vanilla` | Minecraft (Vanilla) | `itzg/minecraft-server:latest` | `[{"container":25565,"protocol":"tcp"}]` | `{"cpu":1.0,"memMb":2048}` |
| `valheim` | Valheim | `lloesche/valheim-server:latest` | `[{"container":2456,"protocol":"udp"},{"container":2457,"protocol":"udp"},{"container":2458,"protocol":"udp"}]` | `{"cpu":2.0,"memMb":4096}` |
| `terraria` | Terraria | `ryshe/terraria:latest` | `[{"container":7777,"protocol":"tcp"}]` | `{"cpu":1.0,"memMb":1024}` |
| `cs2` | Counter-Strike 2 | `joedwards32/cs2:latest` | `[{"container":27015,"protocol":"tcp"},{"container":27015,"protocol":"udp"}]` | `{"cpu":2.0,"memMb":2048}` |
| `rust` | Rust | `didstopia/rust-server:latest` | `[{"container":28015,"protocol":"udp"},{"container":28016,"protocol":"tcp"}]` | `{"cpu":2.0,"memMb":4096}` |

`default_env`, `default_volumes` — пустые (`{}`/`[]`); админ дополняет через PATCH по необходимости.

### Makefile

Заменяем заглушку `seed`:

```make
seed: seed-admin seed-templates

seed-admin:
	set -a && . ./.env && set +a && cd apps/api && uv run python -m gamehost_api.scripts.seed_admin

seed-templates:
	set -a && . ./.env && set +a && cd apps/api && uv run python -m gamehost_api.scripts.seed_templates
```

## Тесты

### `test_templates_service.py`
- list_for_user_returns_only_public
- list_for_admin_returns_all
- create_returns_template; create_duplicate_slug_raises_slug_already_taken
- update_partial_changes_only_provided_fields_and_bumps_updated_at
- update_unknown_id_raises_template_not_found

### `test_templates_routes.py`
- get_unauth_returns_401
- get_as_user_filters_to_public_only
- get_as_admin_returns_all
- post_as_user_returns_403_forbidden
- post_as_admin_returns_201_camel_payload
- post_duplicate_slug_returns_409
- patch_as_admin_changes_field; patch_unknown_returns_404; patch_invalid_slug_returns_422

### `test_nodes_service.py`
- create_generates_api_key_and_hashes_it (verify with `verify_password(hash, plain)`)
- create_duplicate_name_raises_node_name_taken
- update_status_to_drain_succeeds; update_status_offline_rejected_at_schema_layer (фактически тестируется в routes — на сервисе принимаем только online/drain enum)
- delete_unknown_returns_node_not_found
- verify_api_key_correct_returns_true; wrong_returns_false

### `test_nodes_routes.py`
- post_as_user_returns_403
- post_as_admin_returns_201_with_api_key_in_body
- subsequent_get_does_not_expose_api_key_in_payloads
- patch_status_drain_returns_200; patch_status_offline_returns_422
- delete_returns_204; delete_unknown_returns_404

### `test_seed_scripts.py`
- seed_admin_creates_when_user_missing
- seed_admin_promotes_existing_user_to_admin
- seed_admin_idempotent_on_existing_admin
- seed_admin_exits_when_env_missing (subprocess.run, returncode=1)
- seed_templates_inserts_5_canonical_templates_on_empty_db
- seed_templates_idempotent (run twice → still 5)
- seed_templates_does_not_overwrite_admin_edits (PATCH between runs preserves changes)

### Coverage gates (без изменений)
- общий `--cov-fail-under=70`
- domain ≥ 85% (проверять локально через targeted run)

## Definition of Done

- [ ] Миграция `0002_templates_nodes` `upgrade head` и `downgrade base` чистые.
- [ ] `make seed` идемпотентно создаёт/апгрейдит admin и наполняет 5 шаблонов.
- [ ] Все эндпоинты `/api/v1/templates` и `/api/v1/nodes` работают согласно таблицам выше.
- [ ] Admin-only эндпоинты возвращают 403 для user (RFC 7807).
- [ ] `POST /nodes` возвращает `apiKey` ровно один раз; `GET`/`PATCH` его не отдают.
- [ ] PATCH `/nodes/{id}` со `status='offline'` отбивается 422.
- [ ] `make lint typecheck test` зелёные; coverage ≥ 70% (domain ≥ 85%).
- [ ] `.env.example` дополнен `BOOTSTRAP_ADMIN_EMAIL`, `BOOTSTRAP_ADMIN_PASSWORD`.
- [ ] README получает раздел «Stage 2: templates & nodes» с `curl`-примерами и упоминанием `make seed`.

## Verification вручную

1. `make migrate && make seed` → лог о создании admin и 5 templates.
2. login как admin → `GET /api/v1/templates` → 5 объектов в JSON.
3. `POST /api/v1/nodes` с admin JWT → 201, ответ содержит `apiKey`.
4. `GET /api/v1/nodes/{id}` → `apiKey` в ответе **отсутствует**.
5. `PATCH /api/v1/nodes/{id}` со `status: "drain"` → 200; `status: "offline"` → 422 + `code:validation_error`.
6. login как user → `POST /templates` → 403 + `code:forbidden`.
7. `DELETE /api/v1/nodes/{id}` → 204; повторный → 404.

## Что НЕ входит в Stage 2

- `DELETE /templates` — гасим через `is_public=false`.
- Health-check нод (`last_seen_at`, авто-перевод в `offline`) — Stage 4+.
- Использование API-ключа ноды на стороне node-agent — Stage 3.
- Audit log admin-действий — Stage 8.
- Защита `DELETE /nodes` от удаления нод с активными серверами — Stage 4 (там появятся `servers`).

## Следующий шаг

После реализации Stage 2 и зелёного CI — отдельный цикл brainstorm → spec → plan для **Stage 3 (node-agent: FastAPI на ноде, Docker SDK, Bearer-API-key)**.

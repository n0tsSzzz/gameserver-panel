# Stage 6 — Members & Roles

**Дата:** 2026-05-03
**Источник ТЗ:** `claude_code_prompt.md` (§3, §5, §6, §7, §9 Stage 6)
**Предыдущий этап:** Stage 5 (logs) — PR #6, в `main`.

## Context

После Stage 5 пользователь может полностью управлять своим сервером и видеть логи. Stage 6 даёт возможность пригласить друзей: владелец генерирует email-bound invite-ссылку, друг логинится и принимает. После принятия — он member со ролью `viewer` (только чтение) или `operator` (start/stop/restart + чтение). Все mutating-эндпоинты `/servers/{id}/*` теперь имеют RBAC-проверку.

### Решённые ключевые развилки

- **Token semantics (ответ A):** email-bound. Invite привязан к email; принимающий должен быть залогинен под аккаунтом с тем же email — иначе 403. Утечка ссылки бесполезна без аккаунта на нужный email.
- **owner role**: implicit через `servers.owner_id`, не дублируется в `server_members`.
- **Token storage**: plaintext возвращается один раз на POST invite; в БД хранится `sha256(token).hex()`.
- **TTL invite**: 7 дней.
- **Email sending**: вне scope (нет SMTP); владелец делится ссылкой вручную.

## Архитектура

```
   Owner (A)              Friend (B with friend@x.test)
       │                              │
       │ POST /members/invite         │
       │ {email, role}                │
       ▼                              │
   server_invites                     │
   (token_hash, expires_at, ...)      │
       │                              │
       │ ← responds {token, url}      │
       │ shares URL out-of-band       │
       │  ────────────────────────────┤
       │                              ▼
       │                 POST /invites/{token}/accept
       │                 (Bearer of B)
       │                              │
       │           email match check  │
       │                              ▼
       │                     server_members row
       │                  (server_id, user_id, role)
```

## Структура каталогов (изменения)

```
apps/api/
├── alembic/versions/0004_members_invites.py
└── src/gamehost_api/
    ├── api/v1/
    │   ├── members.py             # NEW
    │   ├── invites.py             # NEW (accept/preview)
    │   ├── deps.py                # +require_server_role
    │   └── servers.py             # apply RBAC to lifecycle endpoints
    ├── db/models/
    │   ├── server_member.py
    │   └── server_invite.py
    ├── db/models/__init__.py      # +exports
    ├── domain/
    │   ├── exceptions.py          # +5 new
    │   ├── members.py             # MemberService
    │   └── access.py              # NEW: get_server_member_role + role-rank helper
    ├── repositories/
    │   ├── server_members.py
    │   └── server_invites.py
    └── schemas/
        └── members.py
```

`packages/shared/src/gamehost_shared/enums.py`: `ServerRole` (`OWNER/OPERATOR/VIEWER`).

## Модель данных

### `server_members`

| column | type | constraints |
|---|---|---|
| `server_id` | UUID | NOT NULL FK→servers.id ON DELETE CASCADE |
| `user_id` | UUID | NOT NULL FK→users.id ON DELETE CASCADE |
| `role` | text | NOT NULL CHECK `IN ('viewer','operator')` |
| `invited_by` | UUID | NULL FK→users.id ON DELETE SET NULL |
| `created_at` | timestamptz | NOT NULL DEFAULT now() |

PK `(server_id, user_id)`. Index by `user_id` (для запроса «всех серверов где я member»).

### `server_invites`

| column | type | constraints |
|---|---|---|
| `id` | UUID | PK |
| `server_id` | UUID | NOT NULL FK→servers.id ON DELETE CASCADE |
| `email` | text | NOT NULL (нормализован lower) |
| `role` | text | NOT NULL CHECK `IN ('viewer','operator')` |
| `token_hash` | text | NOT NULL UNIQUE |
| `expires_at` | timestamptz | NOT NULL |
| `accepted_at` | timestamptz | NULL |
| `accepted_by` | UUID | NULL FK→users.id ON DELETE SET NULL |
| `revoked_at` | timestamptz | NULL |
| `created_by` | UUID | NOT NULL FK→users.id ON DELETE CASCADE |
| `created_at` | timestamptz | NOT NULL DEFAULT now() |

Индексы: PK; `UNIQUE (token_hash)`; partial unique:
```sql
CREATE UNIQUE INDEX uq_server_invites_open
  ON server_invites (server_id, lower(email))
  WHERE accepted_at IS NULL AND revoked_at IS NULL;
```

### Миграция `0004_members_invites`

Hand-written. `upgrade` создаёт обе таблицы с CHECK + индексами. `downgrade` дропает в обратном порядке.

## Эндпоинты

### `/api/v1/servers/{id}/members`

| Метод | Кто | Поведение |
|---|---|---|
| `GET` | owner / member / admin | Список members с ролями + owner row отдельно. 404 если не your server. |
| `POST /invite` | owner / admin | `{email, role}`. Нормализует email lower(). Конфликты: 409 `member_exists` или 409 `invite_exists`. На успех — генерит `secrets.token_urlsafe(32)`, хранит `sha256(token)`, возвращает `{inviteId, token, expiresAt, inviteUrl}` (token виден один раз). audit_log `member.invited`. |
| `DELETE /{userId}` | owner / admin / self | owner удалить нельзя (404 если userId == server.owner_id). 204. audit_log `member.removed`. |

### `/api/v1/servers/{id}/invites`

| Метод | Кто | Поведение |
|---|---|---|
| `GET` | owner / admin | Список открытых invites (без token). 404 если не owner. |
| `DELETE /{inviteId}` | owner / admin | revoked_at = now(). 204. audit_log `invite.revoked`. |

### `/api/v1/invites/{token}`

| Метод | Кто | Поведение |
|---|---|---|
| `GET` | любой залогиненный | Preview: `{serverId, serverName, role, email, expiresAt}`. 404 если token unknown / expired / revoked / accepted. |
| `POST /accept` | любой залогиненный | Валидация: token-lookup → exists / not expired / not revoked / not accepted; **email == current_user.email** → иначе 403 `invite_email_mismatch`; не уже member → иначе 409 `member_exists`. Создаёт `server_members` row, помечает invite accepted. Возвращает `{serverId, role}`. audit_log `member.accepted`. |

### Расширение existing endpoints

- `GET /api/v1/servers`: возвращает servers где `owner_id = me OR EXISTS server_members(server_id, user_id=me)`. Admin → все.
- `GET /api/v1/servers/{id}`: 404 если не owner / member / admin.
- `GET /servers/{id}/logs*`, `POST stream-token`: viewer ✓.
- `POST /servers/{id}/start|stop|restart`: operator ✓ (требует RBAC dep).
- `PATCH /servers/{id}`, `DELETE`: owner / admin only.

### RBAC dep `require_server_role`

В `api/v1/deps.py`:

```python
_RANK = {"viewer": 1, "operator": 2, "owner": 3}


async def get_server_role_for(
    session: AsyncSession, server_id: uuid.UUID, user: User
) -> str | None:
    """Returns 'owner' | 'operator' | 'viewer' | None.
    None means user has no relationship with server (unless they're admin)."""
    if user.role == "admin":
        return "owner"  # admin acts as owner
    srv = await ServersRepository(session).get(server_id)
    if srv is None:
        return None
    if srv.owner_id == user.id:
        return "owner"
    member = await ServerMembersRepository(session).get(server_id, user.id)
    if member is not None:
        return member.role
    return None


def require_server_role(min_role: Literal["viewer", "operator", "owner"]):
    async def _dep(
        server_id: uuid.UUID,
        user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_session),
    ) -> str:
        role = await get_server_role_for(session, server_id, user)
        if role is None:
            raise ServerNotFound(str(server_id))  # 404 to hide existence
        if _RANK[role] < _RANK[min_role]:
            raise Forbidden(f"requires server role >= {min_role}")
        return role
    return _dep
```

Подключение к существующим эндпоинтам:
- `start/stop/restart` — `dependencies=[Depends(require_server_role("operator"))]`
- `patch/delete` — `dependencies=[Depends(require_server_role("owner"))]`
- `get_server`, `logs/*` — `dependencies=[Depends(require_server_role("viewer"))]`

`ServerService` методы остаются без изменений: dep уже отбила доступ; внутренний `get_for` оставляем для admin/owner-only operations через service-layer (delete service-метод). Дублирование 404-логики безопасно.

## DTOs (`schemas/members.py`)

```python
class MemberInviteIn(CamelModel):
    email: Email   # reused from auth schemas
    role: Literal["viewer", "operator"]


class MemberInviteOut(CamelModel):
    invite_id: uuid.UUID
    token: str
    expires_at: datetime
    invite_url: str


class InvitePreviewOut(CamelModel):
    server_id: uuid.UUID
    server_name: str
    role: Literal["viewer", "operator"]
    email: str
    expires_at: datetime


class InviteAcceptOut(CamelModel):
    server_id: uuid.UUID
    role: Literal["viewer", "operator"]


class MemberOut(CamelModel):
    user_id: uuid.UUID
    email: str
    role: Literal["owner", "viewer", "operator"]
    invited_by: uuid.UUID | None
    created_at: datetime


class InviteOut(CamelModel):
    id: uuid.UUID
    email: str
    role: Literal["viewer", "operator"]
    expires_at: datetime
    created_at: datetime
```

`Email` — Annotated str с `validate_email(test_environment=True)` (уже в `schemas/auth.py`; перенесём в `schemas/_email.py` или просто импортируем).

## `MemberService` (`domain/members.py`)

```python
class MemberService:
    def __init__(self, session, *, base_invite_url: str) -> None: ...

    async def list_members(self, server_id: uuid.UUID) -> list[MemberOut]: ...
    async def list_open_invites(self, server_id: uuid.UUID) -> list[ServerInvite]: ...
    async def invite(
        self, server_id: uuid.UUID, email: str, role: str, by_user: User
    ) -> tuple[ServerInvite, str]:  # (row, plain_token)
        ...
    async def revoke_invite(
        self, server_id: uuid.UUID, invite_id: uuid.UUID, by_user: User
    ) -> None: ...
    async def remove_member(
        self, server_id: uuid.UUID, user_id: uuid.UUID, actor: User
    ) -> None: ...
    async def preview_invite(self, plain_token: str) -> InvitePreviewOut: ...
    async def accept_invite(self, plain_token: str, actor: User) -> InviteAcceptOut: ...
```

`base_invite_url` — конфиг (`API_PUBLIC_URL` env, default `http://localhost:8000`); используется только для `inviteUrl` в ответе.

## Расширение `ServerService.list_for`

```python
async def list_for(self, user: User) -> list[Server]:
    if user.role == "admin":
        return await self._servers.list_all()
    return await self._servers.list_for_user_or_member(user.id)
```

`ServersRepository.list_for_user_or_member(user_id)` — JOIN/UNION:

```sql
SELECT * FROM servers
WHERE owner_id = :uid
   OR id IN (SELECT server_id FROM server_members WHERE user_id = :uid)
ORDER BY created_at DESC
```

## Settings

```
api_public_url: str = Field(default="http://localhost:8000", alias="API_PUBLIC_URL")
invite_ttl_days: int = Field(default=7, alias="INVITE_TTL_DAYS")
```

`.env.example`:
```
# Stage 6 — invites
API_PUBLIC_URL=http://localhost:8000
INVITE_TTL_DAYS=7
```

## Тесты

### Repos
`test_server_members_repo.py`, `test_server_invites_repo.py` — create / get / list / delete; partial unique violation.

### Domain
`test_member_service.py`:
- invite_creates_row_returns_plaintext_once
- invite_returns_409_member_exists
- invite_returns_409_invite_exists (open)
- invite_after_revoked_works
- preview_unknown_token_raises
- accept_email_mismatch_raises
- accept_expired_raises
- accept_revoked_raises
- accept_already_accepted_raises
- accept_happy_creates_member_and_marks_invite
- remove_member_owner_cannot_remove_self_via_member_endpoint (returns NotServerMember; UI handles via `DELETE /servers/{id}` to actually delete server)
- remove_member_self_leave

### Routes
`test_members_routes.py` — auth/RBAC matrix:
- GET members: 401/404/200 для owner/operator/viewer; admin везде.
- POST invite: только owner; non-owner → 403; admin ✓; happy → 201.
- POST accept: email mismatch → 403; happy → 200.
- DELETE member: owner; self-leave (member sees their own row in 204).

`test_servers_rbac.py`:
- viewer может GET / logs / stream-token, не может start/stop/patch/delete.
- operator может start/stop/restart, не может patch/delete.
- owner всё, admin = owner-equivalent на чужом сервере.

### Coverage
≥70% общая; `domain/` ≥85% (members service heavily tested).

## Definition of Done

- [ ] Миграция `0004` up/down, partial unique index валидирован.
- [ ] Email-bound invites: один открытый на (server, email); accept проверяет email match.
- [ ] Token plain показан один раз; в БД sha256.
- [ ] RBAC-матрица соблюдена: viewer/operator не могут owner-actions.
- [ ] `GET /servers` включает member-серверы.
- [ ] audit_log: `member.invited`, `member.accepted`, `member.removed`, `invite.revoked`.
- [ ] `make lint typecheck test` зелёные; coverage ≥70%; domain ≥85%.
- [ ] README — секция Stage 6 с примером invite/accept.

## Verification вручную

1. `make migrate`.
2. User A создаёт сервер.
3. User B (с email `friend@x.test`) регистрируется.
4. A: `POST /api/v1/servers/{id}/members/invite {"email":"friend@x.test","role":"operator"}` → token+url.
5. B: `POST /api/v1/invites/{token}/accept` → 200.
6. B: `POST /api/v1/servers/{id}/start` → 202; `PATCH ...` → 403.
7. A: `DELETE /api/v1/servers/{id}/members/{userBId}` → 204.

## Что НЕ входит

- Реальная отправка email — отложено (нет SMTP; владелец шлёт ссылку вручную).
- UI приёма инвайтов — Stage 9 (Next.js).
- Members могут смотреть audit_log — Stage 8.
- transferOwnership — отложено.
- MFA / 2FA — отложено.

## Следующий шаг

После Stage 6 — Stage 7 (backups в S3/MinIO).

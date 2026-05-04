# Stage 7 — Backups (MinIO + tar volume)

**Дата:** 2026-05-04
**Источник ТЗ:** `claude_code_prompt.md` (§5 backups, §6 backups+exec, §9 Stage 7)
**Предыдущий этап:** Stage 6 (members & roles) — PR #7, в `main`.

## Context

Stage 6 закрыл совместное использование сервера. Stage 7 даёт владельцу/операторам делать бэкапы данных игры (Docker volume) в MinIO и восстанавливать их. Архитектурно: API ставит ARQ-задачу → worker зовёт node-agent → node-agent поднимает ephemeral `busybox`-helper с volume mount, гонит `tar | gzip` в stdout, и стримит результат напрямую в MinIO multipart upload. Restore — обратный поток. Тaks-сторона переиспользует Stage 4 паттерны (`tasks` table, ARQ jobs, audit_log).

### Решённые ключевые развилки

- **Tar source (ответ B):** ephemeral helper-container `busybox:latest` с volume RO-mount. Работает независимо от состояния main-контейнера; не требует tar внутри игрового образа.
- **Кто заливает в MinIO:** node-agent (ТЗ §6: «поток в node-agent → multipart upload в MinIO»). Worker не имеет S3-deps.
- **Compression:** tar + gzip. CPU дёшево, место экономим.
- **Volume strategy (brownfield для Stage 4):** worker при provision создаёт named volume `gh-{server_id}-data` и монтирует как `/data`. Это нужно, иначе helper-контейнеру нечего монтировать.
- **RBAC:** GET = viewer+; POST backup = operator+; POST restore = owner-only.
- **DELETE backup, retention/cron, шифрование, скачивание архива** — вне scope.

## Структура (изменения)

```
apps/api/
├── alembic/versions/0005_backups.py
└── src/gamehost_api/
    ├── api/v1/
    │   ├── backups.py
    │   └── __init__.py            # +include backups
    ├── db/models/
    │   ├── backup.py
    │   └── __init__.py            # +export Backup
    ├── domain/
    │   ├── backups.py             # BackupService
    │   └── exceptions.py          # +BackupNotFound, BackupNotReady, RestoreNotAllowed
    ├── repositories/backups.py
    └── schemas/backups.py

apps/worker/
└── src/gamehost_worker/
    ├── jobs/backup.py
    ├── jobs/restore.py
    ├── jobs/__init__.py           # +exports
    └── jobs/_common.py            # build_create_spec teaches named volume gh-{id}-data

apps/node_agent/
├── pyproject.toml                 # +aioboto3>=13,<15
└── src/gamehost_node/
    ├── core/config.py             # +s3_*
    ├── s3_client.py               # NEW
    ├── domain/backups.py          # NEW (BackupOps)
    ├── api/backups.py             # NEW
    ├── api/__init__.py            # +include backups
    ├── schemas/backups.py
    └── main.py                    # bucket auto-create on lifespan
```

`gamehost_shared.enums.TaskKind`: добавляем `BACKUP, RESTORE`.

## Модель данных

### Миграция `0005_backups`

```python
def upgrade():
    op.create_table(
        "backups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("server_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("s3_key", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(), nullable=False, server_default="creating"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["server_id"], ["servers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("s3_key", name="uq_backups_s3_key"),
        sa.CheckConstraint(
            "status IN ('creating','available','failed')", name="ck_backups_status"
        ),
    )
    op.create_index(
        "ix_backups_server_created", "backups",
        ["server_id", sa.text("created_at DESC")],
    )

    # Extend tasks.kind CHECK.
    op.drop_constraint("ck_tasks_kind", "tasks", type_="check")
    op.create_check_constraint(
        "ck_tasks_kind", "tasks",
        "kind IN ('provision','start','stop','restart','delete','backup','restore')",
    )


def downgrade():
    op.drop_constraint("ck_tasks_kind", "tasks", type_="check")
    op.create_check_constraint(
        "ck_tasks_kind", "tasks",
        "kind IN ('provision','start','stop','restart','delete')",
    )
    op.drop_index("ix_backups_server_created", table_name="backups")
    op.drop_table("backups")
```

ORM `Backup`: `id, server_id, s3_key, size_bytes, status, error, created_by, finished_at, created_at`.

## API эндпоинты

| Метод/путь | Кто | Поведение |
|---|---|---|
| `GET /api/v1/servers/{id}/backups` | viewer+ | Список `BackupOut`, `created_at desc`. |
| `POST /api/v1/servers/{id}/backups` | operator+ | Создаёт `backups (status=creating)` + `tasks (kind=backup)`, ARQ enqueue. 202 `{backupId, taskId}`. |
| `GET /api/v1/backups/{id}` | viewer+ (на server-связку) | `BackupOut`. |
| `POST /api/v1/backups/{id}/restore` | owner+ | Сервер должен быть `stopped`; backup `available`; иначе 409 `restore_not_allowed`. Создаёт `tasks (kind=restore)`, ARQ. 202 `{taskId}`. |

DTOs:
```python
class BackupOut(CamelModel):
    id: uuid.UUID
    server_id: uuid.UUID
    size_bytes: int
    status: Literal["creating", "available", "failed"]
    error: str | None
    created_by: uuid.UUID | None
    created_at: datetime
    finished_at: datetime | None

class BackupCreateAcceptedOut(CamelModel):
    backup_id: uuid.UUID
    task_id: uuid.UUID

class RestoreAcceptedOut(CamelModel):
    task_id: uuid.UUID
```

`BackupService` (api): `list_for_server`, `create_pending`, `get_for(actor)`, `request_restore(actor)`. Все авторизуются через `get_server_role_for`.

## Node-agent endpoints

| Метод/путь | Поведение |
|---|---|
| `POST /api/v1/containers/{container_id}/backup` | Body `{volumeName, s3Key}`. Запускает `busybox:latest` с volume RO-mount по `/data`, exec `sh -c "tar -cf - -C /data . \| gzip"`, attach stdout, стримит chunks в `aioboto3 client.upload_fileobj` (multipart) в bucket `s3_bucket`, key `s3Key`. На успех → 200 `{sizeBytes}`. helper container удаляется в `finally`. |
| `POST /api/v1/containers/{container_id}/restore` | Body `{volumeName, s3Key}`. Скачивает из MinIO в `BytesIO` или streaming pipe, запускает busybox с volume RW-mount, exec `sh -c "tar -xzf - -C /data"`, stdin = pipe. **Caller (worker) обязан гарантировать что main container остановлен** — node-agent не проверяет (он не знает, какие контейнеры «main»). 200 `{sizeBytes}`. |

`{container_id}` в URL используется для логирования и audit, но фактический tar делается из volume helper'ом, не из main-контейнера.

`s3_client.py`:
```python
async def make_s3_client() -> aioboto3.Session.client:
    s = get_settings()
    session = aioboto3.Session(
        aws_access_key_id=s.s3_access_key,
        aws_secret_access_key=s.s3_secret_key.get_secret_value(),
        region_name=s.s3_region,
    )
    return session.client("s3", endpoint_url=s.s3_endpoint)
```

В `main.py` lifespan: `await ensure_bucket(settings.s3_bucket)` — `head_bucket` then `create_bucket` (ignore 409 / BucketAlreadyExists).

## Worker jobs

`jobs/backup.py`:

```python
async def backup_server(ctx, task_id_str: str) -> None:
    sm = ctx["sm"]; timeout = ctx["node_agent_timeout_s"]
    task_id = task_uuid(task_id_str)
    backup_id: uuid.UUID | None = None
    server_id: uuid.UUID | None = None
    try:
        async with sm() as s:
            await TasksRepository(s).mark_running(task_id); await s.commit()
            t = await TasksRepository(s).get(task_id)
            payload = t.payload or {}
            backup_id = uuid.UUID(payload["backup_id"])
            server_id = t.server_id
            srv = await ServersRepository(s).get(server_id)
            backup = await BackupsRepository(s).get(backup_id)
            node = await NodeRepository(s).get(srv.node_id)

        async with NodeAgentClient(node, timeout_s=timeout) as client:
            result = await client.backup(
                container_id=srv.container_id,
                volume_name=f"gh-{srv.id}-data",
                s3_key=backup.s3_key,
            )
        async with sm() as s:
            await BackupsRepository(s).mark_available(backup_id, result["sizeBytes"])
            await TasksRepository(s).mark_succeeded(task_id)
            await AuditLogRepository(s).record(
                action="backup.created", target_type="backup", target_id=str(backup_id),
                meta={"server_id": str(server_id), "size_bytes": result["sizeBytes"]},
            )
            await s.commit()
    except Exception as exc:
        async with sm() as s:
            if backup_id:
                await BackupsRepository(s).mark_failed(backup_id, str(exc))
            await TasksRepository(s).mark_failed(task_id, str(exc))
            await s.commit()
        raise
```

`jobs/restore.py`:
- Аналогично: проверка `srv.status == 'stopped'` (в API уже проверено — но мы и здесь подстрахуемся; падаем с `restore_not_allowed` если нет).
- POST node-agent `/restore`.
- audit `backup.restored`.

### Worker provision изменение (brownfield)

`apps/worker/src/gamehost_worker/jobs/_common.py:build_create_spec` — расширяем `volumes`:

```python
def build_create_spec(server, template):
    ...
    return {
        ...
        "volumes": [{"name": f"gh-{server.id}-data", "mountPath": "/data", "readOnly": False}],
        ...
    }
```

Существующие сервера, заведённые до 0005 без volume, могут не пройти backup — это **acceptable** (нет данных). Помечаем в README.

`NodeAgentClient` (worker side) дополняется методами `backup`, `restore`:

```python
async def backup(self, *, container_id, volume_name, s3_key) -> dict:
    r = await self._retry("POST", f"/api/v1/containers/{container_id}/backup",
                          json={"volumeName": volume_name, "s3Key": s3_key})
    if r.status_code != 200:
        raise NodeAgentHTTPError(r.status_code, r.text)
    return r.json()

async def restore(self, *, container_id, volume_name, s3_key) -> dict:
    ... аналогично POST /restore
```

## Конфиг

Node-agent `Settings`:
```
s3_endpoint: str = Field(default="http://localhost:9000", alias="S3_ENDPOINT")
s3_access_key: str = Field(default="minioadmin", alias="S3_ACCESS_KEY")
s3_secret_key: SecretStr = Field(default=SecretStr("minioadmin"), alias="S3_SECRET_KEY")
s3_bucket: str = Field(default="gamehost-backups", alias="S3_BUCKET")
s3_region: str = Field(default="us-east-1", alias="S3_REGION")
```

`.env.example`:
```
# Stage 7 — backups
S3_ENDPOINT=http://localhost:9000
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
S3_BUCKET=gamehost-backups
```

## Тесты

### Node-agent
- `test_backup_routes.py`: POST `/backup` happy → 200, mock `BackupOps.run_backup` returns size. Wrong volume → 404. S3 ошибка → 503 `s3_unavailable` (новое исключение `S3Unavailable`).
- `test_backup_ops.py`: с `aioboto3` mocked (или `moto`) проверить, что upload_fileobj вызывается с правильным bucket+key. busybox-container логика мокается через DockerFacade.
- Real-Docker smoke (`@pytest.mark.docker_real`):
  - Создаём volume `gh-test-vol`, кладём в него файл через short-lived busybox.
  - Поднимаем MinIO via testcontainers (или используем dev MinIO из compose).
  - POST /backup → проверяем bytes>0.
  - Удаляем volume → POST /restore с этим архивом → проверяем что файл вернулся.

### Worker
- `test_backup_jobs.py` (respx-mock node-agent): backup happy → backup row available + size; backup error → row failed; restore happy.

### API
- `test_backups_routes.py`: GET list (RBAC viewer); POST create (operator+, viewer → 403); GET single (404 чужой); POST restore (owner only; running server → 409).

### Coverage
≥70% global; domain ≥85%.

## Definition of Done

- [ ] Миграция `0005`: `backups` таблица + ALTER `tasks.kind` CHECK; up/down чистые.
- [ ] node-agent: bucket auto-create на старте; `/containers/{id}/backup` + `/restore`; S3-стрим работает.
- [ ] worker: jobs `backup` / `restore`; provision создаёт named volume `gh-{server_id}-data`.
- [ ] API: 4 эндпоинта; RBAC: viewer get / operator create / owner restore.
- [ ] Restore validates `server.status == 'stopped'` (API + worker).
- [ ] audit_log: `backup.created`, `backup.restored`, `backup.failed`.
- [ ] `make lint typecheck test` зелёные; coverage ≥70%; domain ≥85%.
- [ ] README — секция Stage 7 + brownfield-предупреждение для серверов без volume (созданных до этого этапа).

## Verification вручную

1. `make migrate seed up`. MinIO bucket автосоздастся при старте node-agent.
2. node-agent перезапустить с `S3_*` env'ами.
3. Создать новый сервер (provision применит named volume).
4. Записать данные: `docker exec gh-<sid> sh -c "echo hello > /data/test.flag"`.
5. `POST /api/v1/servers/{sid}/backups` → 202 + taskId/backupId. Подождать `available`.
6. `GET /api/v1/backups/{bid}` → status=available, sizeBytes>0.
7. `POST /api/v1/servers/{sid}/stop` → wait stopped.
8. Удалить файл: `docker exec gh-<sid> rm /data/test.flag`. (Сервер stopped — exec может не работать; альтернатива — busybox-helper тут же.)
9. `POST /api/v1/backups/{bid}/restore` → 202; через несколько секунд файл вернулся.
10. MinIO console показывает `gamehost-backups/{sid}/{bid}.tar.gz`.

## Что НЕ входит

- Расписание автобэкапов (cron) — Stage 8/10.
- Шифрование объектов — отложено.
- DELETE backup endpoint и retention policy — отложено.
- Скачивание архива пользователю — отложено.
- Cross-region replication — вне scope MVP.

## Следующий шаг

Stage 8: метрики, дашборды, аудит-UI (Prometheus + Grafana + Loki + Promtail).

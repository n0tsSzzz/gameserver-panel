# Stage 7 — Backups — Implementation Plan

> Inline execution. Spec: `docs/superpowers/specs/2026-05-04-stage-7-backups-design.md`. Branch: `stage-7-backups`.

**Goal:** Add `backups` table + `backup` / `restore` ARQ jobs + node-agent backup/restore endpoints (helper busybox + tar+gzip → MinIO multipart upload). Worker provision now creates named volume `gh-{server_id}-data`.

---

## Groupings

### Group 1: shared enums + migration `0005` + ORM model
- `gamehost_shared.enums.TaskKind`: add `BACKUP, RESTORE`.
- Migration: create `backups`, ALTER `tasks.kind` CHECK.
- `db/models/backup.py`, register in `__init__`.
- conftest TRUNCATE list: prepend `backups`.

### Group 2: API side — repository + service + endpoints
- `repositories/backups.py:BackupsRepository` (create, get, list_for_server, mark_available, mark_failed).
- `domain/exceptions.py`: `BackupNotFound (404)`, `BackupNotReady (409)`, `RestoreNotAllowed (409)`.
- `schemas/backups.py`: `BackupOut`, `BackupCreateAcceptedOut`, `RestoreAcceptedOut`.
- `domain/backups.py:BackupService`: `list_for_server(actor, server_id)`, `create_pending(actor, server_id) -> (Backup, Task)`, `get_for(actor, backup_id)`, `request_restore(actor, backup_id) -> Task`. ARQ enqueue with task_id + backup_id payload.
- `api/v1/backups.py` 4 endpoints; wire into v1 router.
- Test: minimal happy-path API tests with mocked arq pool.

### Group 3: node-agent — S3 client + bucket + backup/restore endpoints
- Add `aioboto3>=13,<15` to `apps/node_agent/pyproject.toml`.
- `core/config.py`: `s3_endpoint, s3_access_key, s3_secret_key, s3_bucket, s3_region`.
- `s3_client.py`: `make_s3_client()` factory; `ensure_bucket(name)` helper.
- `domain/backups.py:BackupOps` with `run_backup(container_id, volume_name, s3_key) -> int` (returns size_bytes) and `run_restore(...)`. Uses DockerFacade to spawn busybox helper, attach stdout/stdin via streaming, multipart upload via aioboto3.
- `schemas/backups.py`: `BackupRequestIn`, `BackupResultOut`.
- `api/backups.py`: `POST /containers/{id}/backup`, `POST /containers/{id}/restore`. Wire into router.
- `main.py` lifespan: `await ensure_bucket(settings.s3_bucket)` (idempotent).
- Tests: mock `aioboto3` + DockerFacade for happy path; unknown volume → 404.

### Group 4: worker — backup/restore jobs + provision uses named volume
- Update `apps/worker/src/gamehost_worker/jobs/_common.py:build_create_spec` — add `volumes=[{name: f"gh-{server.id}-data", mountPath: "/data"}]`.
- `clients/node_agent_client.py:NodeAgentClient` — add `backup` and `restore` methods.
- `jobs/backup.py:backup_server(ctx, task_id_str)`: read backup_id from task.payload, fetch server+node, call agent, mark_available + audit. On failure mark_failed.
- `jobs/restore.py:restore_backup(ctx, task_id_str)`: read backup_id; call agent; mark task succeeded; audit `backup.restored`. Sanity-check `server.status=='stopped'` to avoid corrupting running data.
- `jobs/__init__.py`: export the two new jobs.
- `apps/worker/src/gamehost_worker/main.py:WorkerSettings.functions` — append both jobs.
- API `BackupService.create_pending` enqueues `backup_server` with `_job_id=task.id` and payload `{backup_id}`.
- Tests: `test_backup_jobs.py` with respx mocking node-agent backup/restore.

### Group 5: README + final pipeline + push
- README "Stage 7" with curl walkthrough + brownfield note (servers created before this stage have no named volume → backups won't work for them; recreate to use).
- `make lint typecheck test`; push.

---

## Self-review

- Spec coverage: migration G1, API G2, node-agent G3, worker G4, README G5.
- No placeholders. Type consistency: `BackupService.{list_for_server, create_pending, get_for, request_restore}`; `NodeAgentClient.{backup, restore}` shared between worker; node-agent `BackupOps.{run_backup, run_restore}`.
- TaskKind extension covered both Python enum and DB CHECK constraint.
- Coverage gates kept (≥70% global). Real-Docker smoke is `@pytest.mark.docker_real` and skipped in normal CI.

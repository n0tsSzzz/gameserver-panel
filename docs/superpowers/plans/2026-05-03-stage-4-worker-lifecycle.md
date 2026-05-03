# Stage 4 — Worker + base lifecycle — Implementation Plan

> Inline execution. Spec: `docs/superpowers/specs/2026-05-03-stage-4-worker-lifecycle-design.md`. Branch: `stage-4-worker-lifecycle`.

**Goal:** Build the full server-provisioning loop: api enqueues ARQ task → worker picks up → calls node-agent → Docker container running, server.host:port persisted.

**Tech Stack:** ARQ on Redis, httpx async client (with respx for mocks in tests), SQLAlchemy 2.0 async, Alembic migration `0003`, FastAPI 202 Accepted pattern.

---

## Task groupings (each lands as a single commit after `make lint typecheck test` is green)

### Group 1: shared enums + Resources move + node.api_key migration
- Add `TaskKind`, `TaskStatus` to `gamehost_shared.enums`.
- Move `Resources` schema from `gamehost_node/schemas/containers.py` to `gamehost_shared/resources.py` (keep re-export at old location).
- Migration `0003_servers_tasks_audit` step 1: `nodes` — DROP `api_key_hash`, ADD `api_key text NOT NULL` with `server_default=''` to satisfy non-null on existing rows; backfill not needed in dev (we re-seed).
- Update `apps/api/src/gamehost_api/db/models/node.py`: replace `api_key_hash` field with `api_key`.
- Update `apps/api/src/gamehost_api/repositories/nodes.py:create()` parameter name `api_key_hash` → `api_key`.
- Update `apps/api/src/gamehost_api/domain/nodes.py:NodeService.create()`: store plaintext directly (no `hash_password` for the api key); drop the now-unused `verify_api_key`.
- Update existing api tests (`test_nodes_routes.py`, `test_nodes_service.py`, `test_nodes_repo.py`, `factories.py:make_node`) to use `api_key` field.

### Group 2: ORM models for Server, Task, AuditLog + migration `0003`
- Create `db/models/server.py`, `db/models/task.py`, `db/models/audit_log.py`.
- Update `db/models/__init__.py` exports.
- Hand-write migration `0003_servers_tasks_audit.py`: nodes column swap + create the three new tables with all CHECKs and indexes per spec.
- Verify `alembic upgrade head` and `downgrade base` clean against live Postgres.
- Conftest TRUNCATE list: add `audit_log, tasks, servers` (FK-respecting order).

### Group 3: domain exceptions + repositories
- Add to `apps/api/src/gamehost_api/domain/exceptions.py`: `ServerNotFound (404)`, `NoCapacity (409)`, `InvalidServerState (409)`, `ContainerMissing (internal — not exposed via HTTP)`.
- Repositories under `apps/api/src/gamehost_api/repositories/`:
  - `servers.py:ServersRepository` — `create`, `get`, `list_for_owner`, `list_all`, `set_status`, `set_provisioned`, `update_fields`, `delete_row`.
  - `tasks.py:TasksRepository` — `create`, `get`, `mark_running`, `mark_succeeded`, `mark_failed`.
  - `audit_log.py:AuditLogRepository` — `record(...)`.
- Repo unit tests (one file each, mirror Stage 1/2 patterns).

### Group 4: ARQ pool wiring on api side + schemas + node selector
- `apps/api/src/gamehost_api/tasks/__init__.py` empty; `arq_pool.py:get_arq_pool(app)` plus lifespan create/close.
- Update `main.py:lifespan` to create + close arq pool, store on `app.state.arq_pool`.
- `schemas/servers.py` — `ServerCreateIn`, `ServerPatchIn`, `ServerOut`, `AcceptedOut`, `TaskOut`. `Resources` imported from shared.
- `domain/node_selector.py:least_loaded(session, resources)` — raw SQL per spec.
- `domain/servers.py:ServerService` — entry methods that produce both DB rows + enqueue ARQ:
  - `create(payload, owner)` → `(Server, Task)`
  - `request_action(server_id, kind, actor)` → `Task` (with admissibility checks)
  - `patch(server_id, payload, actor)` → `Server` (sync, requires stopped)
  - `delete(server_id, actor)` → `Task` (sets deleting+enqueue)
- Tests: `test_node_selector.py`, `test_server_service.py` (use mock arq pool).

### Group 5: server + task routers
- `api/v1/servers.py` — 8 endpoints from spec.
- `api/v1/tasks.py` — `GET /tasks/{id}` with auth.
- Wire into `api/v1/__init__.py`.
- Tests: `test_servers_routes.py` (~12 cases), `test_tasks_routes.py` (3 cases).

### Group 6: worker scaffold (apps/worker)
- New uv workspace member `gamehost-worker` with deps: arq, httpx, sqlalchemy[asyncio], asyncpg, structlog, gamehost-shared, **AND** depend on `gamehost-api` for repositories/domain reuse — actually no: spec says worker has DB but **not** through api package. We import repositories/models from api package via the workspace `gamehost-api` dependency (workspace package is importable since shared models). Mark the dependency.
- `core/config.py` worker settings.
- `core/logging.py` (copy of node-agent's structlog config).
- `clients/node_agent_client.py` — httpx async + retry.
- `jobs/_common.py` — sm helpers, status transitions; `jobs/{provision,start,stop,restart,delete}.py`.
- `main.py` — `WorkerSettings` ARQ entry.
- Dockerfile (multi-stage uv).
- Add `worker` service to `deploy/docker-compose.yml`.
- Makefile: `build-worker` target.

### Group 7: worker tests (e2e via respx)
- `apps/worker/tests/conftest.py` — Postgres testcontainer + alembic upgrade; redis URL via existing docker-compose redis (or testcontainers redis); respx fixture; truncate per-test.
- `tests/test_node_selector.py` — moved here (or kept in api — spec says `apps/api/src/gamehost_api/domain/node_selector.py` so tests live in api).
- `tests/test_jobs_e2e.py` — 6 cases per spec.

### Group 8: README + final polish
- README "Stage 4" section with `make build-worker` and full happy-path walkthrough.
- Final lint/typecheck/test pass; coverage check; push branch.

---

## Self-review

- Spec coverage: all sections of spec mapped to groups (1-8). nodes.api_key swap is in G1; servers/tasks/audit_log in G2; node_selector in G4; worker jobs in G6; e2e in G7.
- No placeholders.
- Type consistency: `ServerService.{create, request_action, patch, delete}` consistent across G4, G5; `NodeAgentClient.{create_container, get_container, start, stop, restart, delete}` consistent G6, G7; repository signatures locked in G3.
- Coverage gates: 70% global, 85% on `apps/api/src/gamehost_api/domain` and `apps/worker/src/gamehost_worker/jobs`.

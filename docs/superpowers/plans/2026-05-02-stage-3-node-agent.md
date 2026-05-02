# Stage 3 — node-agent — Implementation Plan

> Inline execution. Spec: `docs/superpowers/specs/2026-05-02-stage-3-node-agent-design.md`. Branch: `stage-3-node-agent` (already created with spec commit).

**Goal:** Build the node-agent FastAPI service: container lifecycle through Docker SDK, Bearer-API-key auth, SSE log stream, security defaults from spec §7.

**Tech Stack:** FastAPI, `docker` SDK (synchronous, wrapped via `asyncio.to_thread`), pytest with mock-based tests + one optional real-Docker smoke under `@pytest.mark.docker_real`.

---

## Task groupings

Each group lands as a single commit after `make lint typecheck test` is green.

### Group 1: shared CamelModel migration + workspace wiring
- Move `apps/api/src/gamehost_api/schemas/base.py:CamelModel` → `packages/shared/src/gamehost_shared/camel_model.py`. Add `pydantic` to shared deps. Re-export from old location for non-disruptive consumers.
- Add `apps/node_agent` to `[tool.uv.workspace] members`. Add `mypy_path` entry.
- Register `pytest` markers and `addopts` exclusion: `markers = ["docker_real: requires Docker daemon"]`, `addopts += ' -m "not docker_real"'`.
- Run `uv sync --all-packages`; existing api tests still green.

### Group 2: node-agent skeleton + config + auth + healthz
- `apps/node_agent/pyproject.toml` (gamehost-node, deps: fastapi, uvicorn, docker, structlog, pydantic-settings, gamehost-shared).
- `src/gamehost_node/__init__.py`, `core/config.py` (Settings: api_key, docker_host, log_level, default_network, listen_port).
- `core/logging.py` — copy of api's structlog config (no add_logger_name).
- `core/auth.py` — `require_api_key` dep with `hmac.compare_digest`.
- `core/errors.py` — `NodeAgentError` base + RFC 7807 handler (mirror api's `core/errors.py`).
- `domain/exceptions.py` — `ContainerNotFound` (404), `ContainerNameTaken` (409), `DockerUnavailable` (503).
- `api/health.py` — `GET /healthz`.
- `main.py` — FastAPI app, lifespan, register handlers, include health router.
- `tests/conftest.py` — env fixture (`NODE_AGENT_API_KEY=test123`), `client` AsyncClient.
- `tests/test_auth.py` — 4 tests: missing bearer/wrong key/healthz public/correct key.

### Group 3: schemas + DockerFacade
- `schemas/containers.py` — `PortBinding`, `VolumeMount`, `Resources`, `CreateContainerIn`, `ContainerOut`, `ContainerStatsOut`. Use `CamelModel` from shared.
- `docker_facade.py` — `DockerFacade` class with: `create_and_start`, `start`, `stop`, `restart`, `remove`, `inspect`, `stats`, `stream_logs`. Each method `await asyncio.to_thread(...)` over a sync helper. Apply security defaults inside `create_and_start`: `cap_drop=["ALL"]`, `security_opt=["no-new-privileges:true"]`, `read_only=spec.read_only_root`, `tmpfs={"/tmp":""}` when read-only, `nano_cpus`, `mem_limit`, `restart_policy`, `labels`. Map docker SDK exceptions in **service** layer (Group 4), not facade — facade just lets them propagate.
- Status normalisation helper `_normalize_status(docker_state, exit_code)` → `pending|running|stopped|failed`.

### Group 4: ContainerService + container routes + tests
- `domain/containers.py:ContainerService` — wraps `DockerFacade`. Methods `create`, `get`, `start`, `stop`, `restart`, `delete`, `stats`. Each `try/except` mapping `docker.errors.NotFound` → `ContainerNotFound`, `docker.errors.APIError` (status_code 409) → `ContainerNameTaken`, anything else from docker → `DockerUnavailable`.
- `api/containers.py` — router under `/api/v1/containers`, dependency `[Depends(require_api_key)]`, endpoints: POST `""` (201, `ContainerOut`), GET `"/{id}"` (combined `ContainerOut & ContainerStatsOut`), POST `"/{id}/start|stop|restart"` (200 with `ContainerOut`), DELETE `"/{id}"` (204), GET `"/{id}/logs/stream"` (SSE).
- `api/__init__.py` — assemble `api_v1 = APIRouter(prefix="/api/v1")`, include containers + health.
- `main.py` — include `api_v1`. Provide `get_docker_facade` dep so tests can override.
- `tests/factories.py` — helpers to build `MagicMock(spec=DockerFacade)` with sensible default returns.
- `tests/conftest.py` — fixture `mock_facade` returning `AsyncMock(spec=DockerFacade)`; `client` overrides dep.
- `tests/test_containers_service_mock.py` — 7 tests: create happy, name-taken, get unknown 404, start/stop idempotent, delete, docker connection error → 503.
- `tests/test_containers_routes_mock.py` — 9 tests: POST happy + camelCase response, GET 200 + stats, GET unknown 404, POST start/stop/restart 200, DELETE 204, POST 409, ConnectionError 503, SSE returns text/event-stream and yields chunks, POST invalid resources 422.

### Group 5: real-Docker smoke + Dockerfile + Makefile + README
- `tests/test_docker_real.py` under `@pytest.mark.docker_real` — happy-path lifecycle on `busybox:latest`.
- `Dockerfile` (multi-stage builder via uv, runtime as non-root user in group `docker`).
- Makefile: append `build-node-agent` target.
- README: append "Stage 3: node-agent" section with `docker run` example.
- Coverage check: ensure ≥70% on `apps/node_agent` paths.

---

## Self-review

- Spec covers: ✓ scope (Group 1-5), ✓ all 8 endpoints (Groups 2,4), ✓ Bearer auth (Group 2), ✓ security defaults (Group 3 facade), ✓ SSE (Group 4), ✓ mock + real test split (Groups 4,5), ✓ Dockerfile + workspace (Groups 1,5).
- No placeholders. No type drift between groups (`DockerFacade` signatures fixed in Group 3, consumed in Group 4).
- Coverage gate stays at 70% global. Domain ≥85% — exceptions are simple data classes; service is fully mock-tested.

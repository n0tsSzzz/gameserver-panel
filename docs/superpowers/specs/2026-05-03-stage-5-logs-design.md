# Stage 5 — Logs (tail + SSE через Redis pub/sub)

**Дата:** 2026-05-03
**Источник ТЗ:** `claude_code_prompt.md` (§6 servers logs endpoints, §9 Stage 5)
**Предыдущий этап:** Stage 4 (worker + lifecycle) — PR #5, в `main`.

## Context

После Stage 4 пользователь может поднять и удалить игровой сервер, но не видит, что происходит внутри контейнера. Stage 5 даёт два эндпоинта на API:

- `GET /api/v1/servers/{id}/logs?tail=N` — последние N строк (синхронный JSON).
- `GET /api/v1/servers/{id}/logs/stream` — SSE-стрим в реальном времени.

Архитектурный принцип per ТЗ §9: **node-agent публикует строки в Redis pub/sub `logs:{container_id}`**, API подписывается. Так несколько API-реплик и несколько одновременных подписчиков получают свои строки без N parallel `docker logs -f` на ноде.

### Решённые ключевые развилки

- **Publisher lifecycle (ответ A):** always-on. Node-agent при `POST /containers` стартует фоновую `asyncio.Task` `docker logs -f → redis publish`; при `DELETE` останавливает. Tail/stream работают вне зависимости от наличия слушателей.
- **Tail source (ответ A):** новый эндпоинт node-agent `GET /api/v1/containers/{id}/logs?tail=N` (внутри `docker logs --tail N`). API проксирует.
- **SSE auth (ответ A):** pre-flight token-эндпоинт. Клиент сначала делает `POST /api/v1/servers/{id}/logs/stream-token` с обычным Bearer, получает короткоживущий JWT (TTL 60s, привязанный к `server_id`), затем открывает `GET /logs/stream?t=<token>` через `EventSource`.
- **Старый node-agent SSE-эндпоинт `/logs/stream`** дропаем — канонический путь теперь через Redis на стороне API.

## Архитектура

```
   POST /containers     ┌─────────────┐
   ────────────────────▶│  node-agent │
                        │  on create: │
                        │  spawn task │
                        │  docker     │
                        │  logs -f    │──┐ publish
                        │             │  │
                        └─────────────┘  ▼
                                  ┌──────────────┐
                                  │ Redis pub/sub│
                                  │ logs:{cid}   │
                                  └──────┬───────┘
                                         │ SUBSCRIBE
                                         ▼
                            ┌────────────────────┐
                            │  API               │
                            │  GET /logs?tail=N  │ ──▶ httpx → node-agent /logs?tail
                            │  POST /stream-token│ ──▶ JWT 60s
                            │  GET /logs/stream  │ ──▶ SSE
                            └────────────────────┘
```

## Структура (изменения)

```
apps/node_agent/
├── pyproject.toml                       # +redis>=5,<6
├── src/gamehost_node/
│   ├── core/config.py                   # +redis_url
│   ├── log_publisher.py                 # NEW
│   ├── docker_facade.py                 # +tail_logs(id, n)
│   ├── domain/containers.py             # +start/stop publisher in create/delete
│   ├── api/containers.py                # +GET /logs?tail; drop /logs/stream
│   ├── schemas/containers.py            # +LogsTailOut
│   └── main.py                          # +log_publisher in app.state, lifespan shutdown
└── tests/
    ├── conftest.py
    ├── test_log_publisher.py
    └── test_logs_routes.py

apps/api/
└── src/gamehost_api/
    ├── core/config.py                   # +log_stream_token_ttl_s
    ├── core/security.py                 # +create_logs_stream_token, decode_logs_stream_token
    ├── api/v1/servers.py                # +3 endpoints
    ├── domain/servers.py                # +get_log_tail, +mint_log_token, +stream_logs_iter
    ├── clients/                         # NEW
    │   ├── __init__.py
    │   └── node_agent_client.py         # тонкий httpx-клиент (для tail)
    └── tasks/redis_pool.py              # NEW
```

## Node-agent: `LogPublisher`

`apps/node_agent/src/gamehost_node/log_publisher.py`:

```python
class LogPublisher:
    def __init__(self, redis_url: str, facade: DockerFacade) -> None:
        self._redis = redis.asyncio.from_url(redis_url, decode_responses=True)
        self._facade = facade
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def start(self, container_id: str) -> None:
        if container_id in self._tasks and not self._tasks[container_id].done():
            return
        self._tasks[container_id] = asyncio.create_task(self._run(container_id))

    async def stop(self, container_id: str) -> None:
        task = self._tasks.pop(container_id, None)
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def shutdown(self) -> None:
        for cid in list(self._tasks):
            await self.stop(cid)
        await self._redis.aclose()

    async def _run(self, container_id: str) -> None:
        channel = f"logs:{container_id}"
        try:
            async for line in self._facade.stream_logs(container_id):
                await self._redis.publish(channel, line.rstrip())
        except asyncio.CancelledError:
            raise
        except Exception:
            structlog.get_logger().exception("log_publisher_failed", cid=container_id)
```

Lifespan в `main.py` создаёт publisher при старте, вызывает `shutdown()` на остановке.

`ContainerService.create` после успешного `create_and_start`:
```python
await self._publisher.start(out.id)
return out
```

`ContainerService.delete` ДО `facade.remove`:
```python
await self._publisher.stop(container_id)
```

`get_docker_facade` уже dependency, добавляем `get_log_publisher = lambda req: req.app.state.log_publisher` и инжектим в сервис через конструктор.

## Node-agent: `GET /containers/{id}/logs?tail=N`

`schemas/containers.py`:
```python
class LogsTailOut(CamelModel):
    lines: list[str]
```

Endpoint:
```python
@router.get("/{container_id}/logs", response_model=LogsTailOut)
async def get_logs_tail(
    container_id: str,
    tail: int = Query(default=200, ge=1, le=10000),
    svc: ContainerService = Depends(_service),
) -> LogsTailOut:
    return LogsTailOut(lines=await svc.tail_logs(container_id, tail))
```

`DockerFacade.tail_logs(container_id, n)` — `await asyncio.to_thread(c.logs, tail=n, stream=False, timestamps=False)`, decode, splitlines.

`ContainerService.tail_logs` оборачивает в стандартный exception mapping.

**Старый эндпоинт `/{container_id}/logs/stream` удаляется** (вместе с тестами и mock'ами).

## API side

### Settings + redis pool

`apps/api/src/gamehost_api/core/config.py` дополнения:
```python
log_stream_token_ttl_s: int = Field(default=60, alias="LOG_STREAM_TOKEN_TTL_S")
```

`apps/api/src/gamehost_api/tasks/redis_pool.py`:
```python
import redis.asyncio as aioredis

async def create_redis_pool(redis_url: str) -> aioredis.Redis:
    return aioredis.from_url(redis_url, decode_responses=True)
```

`main.py` lifespan: создаёт `app.state.redis = await create_redis_pool(settings.redis_url)`; на shutdown — `await redis.aclose()`.

### JWT для streamtoken

`core/security.py` дополнения:

```python
def create_logs_stream_token(*, server_id: uuid.UUID) -> tuple[str, datetime]:
    settings = get_settings()
    now = datetime.now(UTC)
    exp = now + timedelta(seconds=settings.log_stream_token_ttl_s)
    payload = {
        "sub": str(server_id),
        "type": "logs_stream",
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    token = jwt.encode(payload, settings.secret_key.get_secret_value(), algorithm=_ALGORITHM)
    return token, exp


def decode_logs_stream_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        claims = jwt.decode(token, settings.secret_key.get_secret_value(), algorithms=[_ALGORITHM])
    except JWTError as exc:
        raise ValueError("invalid_or_expired_token") from exc
    if claims.get("type") != "logs_stream":
        raise ValueError("wrong_token_type")
    return claims
```

### Эндпоинты `api/v1/servers.py` дополнения

```python
class LogsTailOut(CamelModel):
    lines: list[str]


class StreamTokenOut(CamelModel):
    token: str
    expires_at: datetime


@router.get("/{server_id}/logs", response_model=LogsTailOut)
async def get_server_logs_tail(
    server_id: uuid.UUID,
    tail: int = Query(default=200, ge=1, le=10000),
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> LogsTailOut:
    lines = await _service(request, session).get_log_tail(server_id, tail, user)
    return LogsTailOut(lines=lines)


@router.post("/{server_id}/logs/stream-token", response_model=StreamTokenOut)
async def issue_stream_token(
    server_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> StreamTokenOut:
    token, expires_at = await _service(request, session).mint_log_token(server_id, user)
    return StreamTokenOut(token=token, expires_at=expires_at)


@router.get("/{server_id}/logs/stream")
async def stream_server_logs(
    server_id: uuid.UUID,
    t: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    iter_ = _service(request, session).stream_logs_iter(server_id, t)
    return StreamingResponse(iter_, media_type="text/event-stream")
```

`stream_server_logs` сам авторизуется через токен (без `get_current_user`).

### `ServerService` дополнения

```python
async def get_log_tail(self, server_id, tail, actor) -> list[str]:
    srv = await self.get_for(server_id, actor)  # 404 если не свой
    if srv.container_id is None or srv.node_id is None:
        return []  # ещё не provisioned
    node = await NodeRepository(self._s).get(srv.node_id)
    async with NodeAgentClient(node) as client:
        return await client.tail_logs(srv.container_id, tail)


async def mint_log_token(self, server_id, actor) -> tuple[str, datetime]:
    srv = await self.get_for(server_id, actor)
    return create_logs_stream_token(server_id=srv.id)


async def stream_logs_iter(self, server_id, token) -> AsyncIterator[bytes]:
    try:
        claims = decode_logs_stream_token(token)
    except ValueError as exc:
        raise HTTPException(401, detail=str(exc)) from exc
    if claims["sub"] != str(server_id):
        raise HTTPException(401, detail="token_scope_mismatch")
    srv = await self._servers.get(server_id)
    if srv is None or srv.container_id is None:
        raise HTTPException(404, detail="server_not_provisioned")
    redis = self._redis  # injected
    pubsub = redis.pubsub()
    await pubsub.subscribe(f"logs:{srv.container_id}")
    try:
        async for msg in pubsub.listen():
            if msg["type"] != "message":
                continue
            yield f"data: {msg['data']}\n\n".encode()
    finally:
        await pubsub.unsubscribe()
        await pubsub.close()
```

`ServerService` принимает `redis` в конструктор (через `_service` factory: `ServerService(session, pool, request.app.state.redis)`).

Heartbeat `: ping\n\n` каждые 15 с реализуется через `asyncio.wait_for` на `pubsub.get_message(timeout=15)`.

### `clients/node_agent_client.py` (api side)

Тонкий httpx-клиент **только для tail** (стрим читает API напрямую из Redis). Шаблон точно такой же, как в `apps/worker/.../node_agent_client.py`, но без всех lifecycle-методов:

```python
class NodeAgentClient:
    def __init__(self, node: Node, timeout_s: float = 10.0) -> None: ...
    async def __aenter__(self) -> Self: ...
    async def __aexit__(self, *exc) -> None: ...
    async def tail_logs(self, container_id: str, n: int) -> list[str]: ...
```

(Дублирование — на Stage 8/после Stage 7 имеет смысл вынести общий клиент в `packages/shared`. Не сейчас, чтобы не разрастать рефакторинг.)

## Тесты

### Node-agent
- `test_log_publisher.py`:
  - `start_creates_task_and_publishes_lines_to_redis`: mock facade.stream_logs → 3 строки; `start("c1")`; читаем из Redis (fakeredis), получаем 3 строки.
  - `start_idempotent`: повторный `start` не плодит таск.
  - `stop_cancels_task`: после `stop` нет в `_tasks`, задача завершена.
  - `shutdown_cancels_all`.
- `test_logs_routes.py`:
  - `GET /containers/{id}/logs?tail=10` happy → 200 `{lines: [...]}` (mock facade).
  - 404 unknown → mapping container_not_found.
  - tail вне диапазона → 422.
  - **проверка что старый `/logs/stream` отдаёт 404** (или просто отсутствует в OpenAPI).

### API
- `test_server_logs_routes.py`:
  - `GET /servers/{id}/logs?tail=N` без auth → 401; not-owner → 404; happy: respx-mock node-agent → 200 `{lines}`.
  - `POST /logs/stream-token` happy → 200 + token; not-owner → 404; servers без provisioning тоже отдаёт токен (валидация на `/stream`).
  - `GET /logs/stream?t=<bad>` → 401; with valid token but не provisioned server → 404.
  - happy stream: стартует SSE, в Redis publish 2 строки, читаем 2 chunks из response, проверяем формат `data: …\n\n`.

### Redis
В CI и локально используем уже работающий Redis из `docker-compose` (`make up`). В conftest добавляем session-scope fixture `redis_url` (default `redis://localhost:6379`), `_clean_redis` autouse чтобы каналы не текли.

## DoD

- [ ] node-agent: `LogPublisher` стартует на `create_and_start`, останавливается на `delete`. На shutdown все таски корректно отменяются.
- [ ] node-agent: `GET /containers/{id}/logs?tail=N` отдаёт последние строки. Старый SSE-эндпоинт удалён.
- [ ] api: `GET /servers/{id}/logs?tail=N` проксирует node-agent.
- [ ] api: `POST /servers/{id}/logs/stream-token` отдаёт JWT TTL=60s, scope=`logs_stream`, sub=server_id.
- [ ] api: `GET /servers/{id}/logs/stream?t=<token>` валидирует токен и стримит SSE из Redis.
- [ ] heartbeat (`: ping\n\n`) каждые 15 с при отсутствии новых сообщений.
- [ ] Disconnect клиента → `pubsub.unsubscribe` + close, без leak.
- [ ] `make lint typecheck test` зелёные; общая coverage ≥70%.
- [ ] README — секция Stage 5 + JS-сниппет с EventSource.

## Verification вручную

1. `make up` (есть redis в compose).
2. Поднять node-agent (Stage 3) с `REDIS_URL=redis://host.docker.internal:6379` env.
3. Запустить worker, поднять сервер через `POST /api/v1/servers`.
4. `curl localhost:8000/api/v1/servers/{id}/logs?tail=10 -H "authorization: Bearer $ACCESS"` → последние 10 строк.
5. `TOKEN=$(curl -X POST .../logs/stream-token -H ... | jq -r .token)`.
6. `curl -N ".../logs/stream?t=$TOKEN"` → SSE стрим, видим строки docker по мере появления.
7. Отключиться → проверить логи API: `pubsub.unsubscribe` отработал.

## Что НЕ входит

- Persistent логи в Loki + Promtail — Stage 8.
- Multi-replica fan-out оптимизация (sticky subscriptions) — отложено.
- Авторизация SSE через mTLS / cookie — Stage 10 при появлении прод-деплоя.
- Multi-line log parsing / structured log filtering — отложено.

## Следующий шаг

После Stage 5 — Stage 6 (members & roles: `server_members`, invite-флоу, RBAC).

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gamehost_api.core.security import decode_access_token
from gamehost_api.db.models import User
from gamehost_api.domain.exceptions import Forbidden
from gamehost_api.repositories.users import UserRepository


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    sm: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with sm() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_current_user(
    session: AsyncSession = Depends(get_session),
    authorization: str | None = Header(default=None),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_bearer")
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = decode_access_token(token)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    user = await UserRepository(session).get(uuid.UUID(claims["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user_inactive")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise Forbidden()
    return user


def require_server_role(min_role: str) -> Callable[..., Awaitable[str]]:
    """Dependency factory: returns a dep that resolves the actor's role on the
    server (path param ``server_id``) and raises ServerNotFound if missing or
    Forbidden if the role rank is below ``min_role``.

    Returns the resolved role string ("owner" | "operator" | "viewer").
    """
    from gamehost_api.domain.access import RANK, get_server_role_for
    from gamehost_api.domain.exceptions import ServerNotFound

    async def _dep(
        server_id: uuid.UUID,
        user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_session),
    ) -> str:
        role = await get_server_role_for(session, server_id, user)
        if role is None:
            raise ServerNotFound(str(server_id))
        if RANK[role] < RANK[min_role]:
            raise Forbidden(f"requires server role >= {min_role}")
        return role

    return _dep

import uuid
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.db.models import User
from gamehost_api.repositories.server_members import ServerMembersRepository
from gamehost_api.repositories.servers import ServersRepository

ServerRole = Literal["owner", "operator", "viewer"]
RANK: dict[str, int] = {"viewer": 1, "operator": 2, "owner": 3}


async def get_server_role_for(
    session: AsyncSession, server_id: uuid.UUID, user: User
) -> ServerRole | None:
    if user.role == "admin":
        srv = await ServersRepository(session).get(server_id)
        return "owner" if srv is not None else None
    srv = await ServersRepository(session).get(server_id)
    if srv is None:
        return None
    if srv.owner_id == user.id:
        return "owner"
    member = await ServerMembersRepository(session).get(server_id, user.id)
    if member is None:
        return None
    return "operator" if member.role == "operator" else "viewer"

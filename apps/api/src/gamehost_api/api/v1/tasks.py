import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.api.v1.deps import get_current_user, get_session
from gamehost_api.db.models import User
from gamehost_api.domain.exceptions import ServerNotFound, TaskNotFound
from gamehost_api.repositories.servers import ServersRepository
from gamehost_api.repositories.tasks import TasksRepository
from gamehost_api.schemas.servers import TaskOut

router = APIRouter()


@router.get("/{task_id}", response_model=TaskOut)
async def get_task(
    task_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TaskOut:
    t = await TasksRepository(session).get(task_id)
    if t is None:
        raise TaskNotFound(str(task_id))
    if t.server_id is not None:
        srv = await ServersRepository(session).get(t.server_id)
        if srv is not None and srv.owner_id != user.id and user.role != "admin":
            raise ServerNotFound(str(t.id))
    return TaskOut.model_validate(t)

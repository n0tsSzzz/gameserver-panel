import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.db.models import Task


class TasksRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(
        self,
        *,
        server_id: uuid.UUID | None,
        kind: str,
        payload: dict[str, Any] | None = None,
    ) -> Task:
        t = Task(id=uuid.uuid4(), server_id=server_id, kind=kind, payload=payload or {})
        self._s.add(t)
        await self._s.flush()
        return t

    async def get(self, task_id: uuid.UUID) -> Task | None:
        return await self._s.get(Task, task_id)

    async def mark_running(self, task_id: uuid.UUID) -> None:
        t = await self.get(task_id)
        if t is None:
            return
        t.status = "running"
        t.started_at = datetime.now(UTC)
        await self._s.flush()

    async def mark_succeeded(self, task_id: uuid.UUID) -> None:
        t = await self.get(task_id)
        if t is None:
            return
        t.status = "succeeded"
        t.finished_at = datetime.now(UTC)
        await self._s.flush()

    async def mark_failed(self, task_id: uuid.UUID, error: str) -> None:
        t = await self.get(task_id)
        if t is None:
            return
        t.status = "failed"
        t.error = error
        t.finished_at = datetime.now(UTC)
        await self._s.flush()

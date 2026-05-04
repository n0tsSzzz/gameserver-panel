import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.db.models import Backup


class BackupsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create_with_id(
        self,
        *,
        backup_id: uuid.UUID,
        server_id: uuid.UUID,
        s3_key: str,
        created_by: uuid.UUID | None,
    ) -> Backup:
        row = Backup(
            id=backup_id,
            server_id=server_id,
            s3_key=s3_key,
            status="creating",
            created_by=created_by,
        )
        self._s.add(row)
        await self._s.flush()
        return row

    async def get(self, backup_id: uuid.UUID) -> Backup | None:
        return await self._s.get(Backup, backup_id)

    async def list_for_server(self, server_id: uuid.UUID) -> list[Backup]:
        stmt = (
            select(Backup).where(Backup.server_id == server_id).order_by(Backup.created_at.desc())
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def mark_available(self, backup_id: uuid.UUID, size_bytes: int) -> None:
        b = await self.get(backup_id)
        if b is None:
            return
        b.status = "available"
        b.size_bytes = size_bytes
        b.finished_at = datetime.now(UTC)
        await self._s.flush()

    async def mark_failed(self, backup_id: uuid.UUID, error: str) -> None:
        b = await self.get(backup_id)
        if b is None:
            return
        b.status = "failed"
        b.error = error
        b.finished_at = datetime.now(UTC)
        await self._s.flush()

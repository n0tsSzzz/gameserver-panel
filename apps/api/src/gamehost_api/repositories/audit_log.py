import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.db.models import AuditLog


class AuditLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def record(
        self,
        *,
        action: str,
        target_type: str,
        target_id: str,
        actor_id: uuid.UUID | None = None,
        meta: dict[str, Any] | None = None,
        ip: str | None = None,
    ) -> AuditLog:
        row = AuditLog(
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            meta=meta or {},
            ip=ip,
        )
        self._s.add(row)
        await self._s.flush()
        return row

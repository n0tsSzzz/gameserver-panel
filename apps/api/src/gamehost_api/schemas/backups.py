import uuid
from datetime import datetime
from typing import Literal

from gamehost_shared.camel_model import CamelModel


class BackupOut(CamelModel):
    id: uuid.UUID
    server_id: uuid.UUID
    size_bytes: int
    status: Literal["creating", "available", "failed"]
    error: str | None
    created_by: uuid.UUID | None
    created_at: datetime
    finished_at: datetime | None


class BackupCreateAcceptedOut(CamelModel):
    backup_id: uuid.UUID
    task_id: uuid.UUID


class RestoreAcceptedOut(CamelModel):
    task_id: uuid.UUID

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from gamehost_shared.camel_model import CamelModel
from gamehost_shared.resources import Resources

ServerStatusLiteral = Literal["pending", "provisioning", "running", "stopped", "failed", "deleting"]
TaskKindLiteral = Literal["provision", "start", "stop", "restart", "delete"]
TaskStatusLiteral = Literal["pending", "running", "succeeded", "failed"]


class ServerCreateIn(CamelModel):
    name: str = Field(min_length=1, max_length=100)
    template_id: uuid.UUID
    env_overrides: dict[str, str] = Field(default_factory=dict)
    resources: Resources | None = None


class ServerPatchIn(CamelModel):
    env_overrides: dict[str, str] | None = None
    resources: Resources | None = None


class ServerOut(CamelModel):
    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    template_id: uuid.UUID
    node_id: uuid.UUID | None
    container_id: str | None
    status: ServerStatusLiteral
    host: str | None
    port: int | None
    env_overrides: dict[str, str]
    resources: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class AcceptedOut(CamelModel):
    server_id: uuid.UUID
    task_id: uuid.UUID


class TaskOut(CamelModel):
    id: uuid.UUID
    server_id: uuid.UUID | None
    kind: TaskKindLiteral
    status: TaskStatusLiteral
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime

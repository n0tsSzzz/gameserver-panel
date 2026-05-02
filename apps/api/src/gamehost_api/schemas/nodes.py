import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, HttpUrl

from gamehost_api.schemas.base import CamelModel

WriteableStatus = Literal["online", "drain"]
ReadableStatus = Literal["online", "drain", "offline"]


class NodeCreateIn(CamelModel):
    name: str = Field(min_length=1, max_length=100)
    endpoint_url: HttpUrl
    capacity_cpu: Decimal = Field(gt=0, le=Decimal("999.99"))
    capacity_mem_mb: int = Field(gt=0, le=10_000_000)


class NodePatchIn(CamelModel):
    endpoint_url: HttpUrl | None = None
    capacity_cpu: Decimal | None = Field(default=None, gt=0, le=Decimal("999.99"))
    capacity_mem_mb: int | None = Field(default=None, gt=0, le=10_000_000)
    status: WriteableStatus | None = None


class NodeOut(CamelModel):
    id: uuid.UUID
    name: str
    endpoint_url: str
    capacity_cpu: Decimal
    capacity_mem_mb: int
    status: ReadableStatus
    last_seen_at: datetime | None
    created_at: datetime


class NodeCreateOut(NodeOut):
    api_key: str

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from gamehost_api.db.base import Base, TimestampMixin


class GameTemplate(Base, TimestampMixin):
    __tablename__ = "game_templates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    docker_image: Mapped[str] = mapped_column(String, nullable=False)
    default_env: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    default_ports: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    default_volumes: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    min_resources: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    is_public: Mapped[bool] = mapped_column(nullable=False, server_default="true")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

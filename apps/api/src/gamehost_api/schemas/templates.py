import re
import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import AfterValidator, Field

from gamehost_api.schemas.base import CamelModel

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _validate_slug(value: str) -> str:
    if not _SLUG_RE.fullmatch(value):
        raise ValueError("slug must match ^[a-z0-9][a-z0-9-]{0,63}$")
    return value


Slug = Annotated[str, AfterValidator(_validate_slug)]


class TemplateCreateIn(CamelModel):
    slug: Slug
    display_name: str = Field(min_length=1, max_length=200)
    docker_image: str = Field(min_length=1, max_length=500)
    default_env: dict[str, Any] = Field(default_factory=dict)
    default_ports: list[dict[str, Any]] = Field(default_factory=list)
    default_volumes: list[Any] = Field(default_factory=list)
    min_resources: dict[str, Any] = Field(default_factory=dict)
    is_public: bool = True


class TemplatePatchIn(CamelModel):
    slug: Slug | None = None
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    docker_image: str | None = Field(default=None, min_length=1, max_length=500)
    default_env: dict[str, Any] | None = None
    default_ports: list[dict[str, Any]] | None = None
    default_volumes: list[Any] | None = None
    min_resources: dict[str, Any] | None = None
    is_public: bool | None = None


class TemplateOut(CamelModel):
    id: uuid.UUID
    slug: str
    display_name: str
    docker_image: str
    default_env: dict[str, Any]
    default_ports: list[dict[str, Any]]
    default_volumes: list[Any]
    min_resources: dict[str, Any]
    is_public: bool
    created_at: datetime
    updated_at: datetime

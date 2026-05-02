from datetime import datetime
from typing import Literal

from pydantic import Field

from gamehost_shared.camel_model import CamelModel


class PortBinding(CamelModel):
    container_port: int = Field(gt=0, le=65535)
    host_port: int = Field(gt=0, le=65535)
    protocol: Literal["tcp", "udp"] = "tcp"


class VolumeMount(CamelModel):
    name: str = Field(min_length=1, max_length=200)
    mount_path: str = Field(min_length=1, max_length=500)
    read_only: bool = False


class Resources(CamelModel):
    cpu_cores: float = Field(gt=0, le=128)
    mem_mb: int = Field(gt=0, le=1_000_000)


class CreateContainerIn(CamelModel):
    name: str = Field(min_length=1, max_length=200)
    image: str = Field(min_length=1, max_length=500)
    env: dict[str, str] = Field(default_factory=dict)
    ports: list[PortBinding] = Field(default_factory=list)
    volumes: list[VolumeMount] = Field(default_factory=list)
    resources: Resources
    network: str | None = None
    read_only_root: bool = True


ContainerStatus = Literal["pending", "running", "stopped", "failed"]


class ContainerOut(CamelModel):
    id: str
    name: str
    status: ContainerStatus
    image: str
    created_at: datetime


class ContainerStatsOut(CamelModel):
    cpu_percent: float
    mem_usage_mb: float
    mem_limit_mb: float


class ContainerDetailOut(ContainerOut):
    stats: ContainerStatsOut

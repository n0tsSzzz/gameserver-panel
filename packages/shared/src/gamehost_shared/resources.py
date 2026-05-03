from pydantic import Field

from gamehost_shared.camel_model import CamelModel


class Resources(CamelModel):
    cpu_cores: float = Field(gt=0, le=128)
    mem_mb: int = Field(gt=0, le=1_000_000)

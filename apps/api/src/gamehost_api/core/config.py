from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    database_url: str = Field(
        default="postgresql+asyncpg://gamehost:gamehost@localhost:5432/gamehost",
        alias="DATABASE_URL",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

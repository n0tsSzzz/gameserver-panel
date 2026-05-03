from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    api_key: SecretStr = Field(alias="NODE_AGENT_API_KEY")
    docker_host: str | None = Field(default=None, alias="DOCKER_HOST")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    default_network: str | None = Field(default=None, alias="DEFAULT_NETWORK")
    listen_port: int = Field(default=8080, alias="NODE_AGENT_PORT")
    redis_url: str = Field(default="redis://localhost:6379", alias="REDIS_URL")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

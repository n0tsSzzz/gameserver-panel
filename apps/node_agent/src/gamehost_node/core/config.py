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
    s3_endpoint: str = Field(default="http://localhost:9000", alias="S3_ENDPOINT")
    s3_access_key: str = Field(default="minioadmin", alias="S3_ACCESS_KEY")
    s3_secret_key: SecretStr = Field(default=SecretStr("minioadmin"), alias="S3_SECRET_KEY")
    s3_bucket: str = Field(default="gamehost-backups", alias="S3_BUCKET")
    s3_region: str = Field(default="us-east-1", alias="S3_REGION")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

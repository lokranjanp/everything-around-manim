from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_key: str = "dev-secret"
    internal_render_token: str = "render-secret"
    database_url: str = "sqlite:///./.data/agent.db"
    broker_url: str = "amqp://guest:guest@localhost:5672//"
    model_provider: str = "fake"
    openai_api_key: str | None = None
    openai_text_model: str = "gpt-4o"
    openai_vision_model: str = "gpt-4o"
    render_service_url: str = "http://localhost:8010"
    workflow_mode: str = "inline"
    storage_backend: str = "local"
    storage_root: Path = Path(".data/objects")
    public_base_url: str = "http://localhost:8000"
    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_bucket: str = "manim-artifacts"
    max_attempts: int = Field(default=3, ge=1, le=3)
    critic_threshold: float = Field(default=0.8, ge=0, le=1)


@lru_cache
def get_settings() -> Settings:
    return Settings()


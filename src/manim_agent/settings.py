from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in {None, ""} else default


@dataclass(frozen=True)
class Settings:
    api_key: str = "dev-secret"
    internal_render_token: str = "render-secret"
    database_url: str = "sqlite:///./.data/agent.db"
    langgraph_database_url: str | None = None
    broker_url: str = "amqp://guest:guest@localhost:5672//"
    model_provider: str = "fake"
    openai_api_key: str | None = None
    openai_text_model: str = "gpt-4o"
    openai_vision_model: str = "gpt-4o"
    render_service_url: str = "http://localhost:8010"
    workflow_mode: str = "celery"
    storage_backend: str = "local"
    storage_root: Path = Path(".data/objects")
    public_base_url: str = "http://localhost:8000"
    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_bucket: str = "manim-artifacts"
    max_attempts: int = 3
    critic_threshold: float = 0.8
    checkpoint_retention_days: int = 30

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 3:
            raise ValueError("MAX_ATTEMPTS must be between 1 and 3")
        if not 0 <= self.critic_threshold <= 1:
            raise ValueError("CRITIC_THRESHOLD must be between 0 and 1")


def _load() -> Settings:
    load_dotenv(override=False)
    database_url = _env("DATABASE_URL", "sqlite:///./.data/agent.db") or ""
    return Settings(
        api_key=_env("API_KEY", "dev-secret") or "dev-secret",
        internal_render_token=_env("INTERNAL_RENDER_TOKEN", "render-secret") or "render-secret",
        database_url=database_url,
        langgraph_database_url=_env("LANGGRAPH_DATABASE_URL"),
        broker_url=_env("BROKER_URL", "amqp://guest:guest@localhost:5672//") or "",
        model_provider=_env("MODEL_PROVIDER", "fake") or "fake",
        openai_api_key=_env("OPENAI_API_KEY"),
        openai_text_model=_env("OPENAI_TEXT_MODEL", "gpt-4o") or "gpt-4o",
        openai_vision_model=_env("OPENAI_VISION_MODEL", "gpt-4o") or "gpt-4o",
        render_service_url=_env("RENDER_SERVICE_URL", "http://localhost:8010") or "",
        workflow_mode=_env("WORKFLOW_MODE", "celery") or "celery",
        storage_backend=_env("STORAGE_BACKEND", "local") or "local",
        storage_root=Path(_env("STORAGE_ROOT", ".data/objects") or ".data/objects"),
        public_base_url=_env("PUBLIC_BASE_URL", "http://localhost:8000") or "",
        s3_endpoint_url=_env("S3_ENDPOINT_URL"),
        s3_region=_env("S3_REGION", "us-east-1") or "us-east-1",
        s3_access_key=_env("S3_ACCESS_KEY"),
        s3_secret_key=_env("S3_SECRET_KEY"),
        s3_bucket=_env("S3_BUCKET", "manim-artifacts") or "manim-artifacts",
        max_attempts=int(_env("MAX_ATTEMPTS", "3") or 3),
        critic_threshold=float(_env("CRITIC_THRESHOLD", "0.8") or 0.8),
        checkpoint_retention_days=int(_env("CHECKPOINT_RETENTION_DAYS", "30") or 30),
    )


@lru_cache
def get_settings() -> Settings:
    return _load()

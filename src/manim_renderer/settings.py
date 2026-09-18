from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class RenderSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    internal_render_token: str = "render-secret"
    render_database_url: str = "sqlite:///./.data/renderer.db"
    broker_url: str = "amqp://guest:guest@localhost:5672//"
    render_execution_mode: str = "inline"
    render_runner: str = "fake"
    render_image: str = "agentic-manim-runtime:local"
    kubernetes_namespace: str = "default"
    kubernetes_work_pvc: str = "manim-render-work"
    render_shared_root: Path = Path("/render-shared")
    storage_backend: str = "local"
    storage_root: Path = Path(".data/objects")
    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_bucket: str = "manim-artifacts"
    max_package_bytes: int = 10 * 1024 * 1024


@lru_cache
def get_render_settings() -> RenderSettings:
    return RenderSettings()

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


def _value(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in {None, ""} else default


@dataclass(frozen=True)
class RenderSettings:
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


def _load() -> RenderSettings:
    load_dotenv(override=False)
    return RenderSettings(
        internal_render_token=_value("INTERNAL_RENDER_TOKEN", "render-secret") or "render-secret",
        render_database_url=_value("RENDER_DATABASE_URL", "sqlite:///./.data/renderer.db") or "",
        broker_url=_value("BROKER_URL", "amqp://guest:guest@localhost:5672//") or "",
        render_execution_mode=_value("RENDER_EXECUTION_MODE", "inline") or "inline",
        render_runner=_value("RENDER_RUNNER", "fake") or "fake",
        render_image=_value("RENDER_IMAGE", "agentic-manim-runtime:local") or "",
        kubernetes_namespace=_value("KUBERNETES_NAMESPACE", "default") or "default",
        kubernetes_work_pvc=_value("KUBERNETES_WORK_PVC", "manim-render-work") or "",
        render_shared_root=Path(_value("RENDER_SHARED_ROOT", "/render-shared") or "/render-shared"),
        storage_backend=_value("STORAGE_BACKEND", "local") or "local",
        storage_root=Path(_value("STORAGE_ROOT", ".data/objects") or ".data/objects"),
        s3_endpoint_url=_value("S3_ENDPOINT_URL"),
        s3_region=_value("S3_REGION", "us-east-1") or "us-east-1",
        s3_access_key=_value("S3_ACCESS_KEY"),
        s3_secret_key=_value("S3_SECRET_KEY"),
        s3_bucket=_value("S3_BUCKET", "manim-artifacts") or "manim-artifacts",
        max_package_bytes=int(_value("MAX_PACKAGE_BYTES", str(10 * 1024 * 1024)) or 0),
    )


@lru_cache
def get_render_settings() -> RenderSettings:
    return _load()

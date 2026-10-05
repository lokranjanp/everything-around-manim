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
    database_url: str = "sqlite:///./.data/agent.db"
    model_provider: str = "fake"
    openai_api_key: str | None = None
    openai_text_model: str = "gpt-4o"
    openai_vision_model: str = "gpt-4o"
    storage_root: Path = Path(".data/objects")
    render_runner: str = "docker"
    render_image: str = "agentic-manim-runtime:local"
    max_package_bytes: int = 10 * 1024 * 1024
    max_attempts: int = 3
    critic_threshold: float = 0.8

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 3:
            raise ValueError("MAX_ATTEMPTS must be between 1 and 3")
        if not 0 <= self.critic_threshold <= 1:
            raise ValueError("CRITIC_THRESHOLD must be between 0 and 1")


def _load() -> Settings:
    load_dotenv(override=False)
    data_root = Path(_env("DATA_ROOT", ".data") or ".data")
    return Settings(
        api_key=_env("API_KEY", "dev-secret") or "dev-secret",
        database_url=f"sqlite:///{data_root / 'agent.db'}",
        model_provider=_env("MODEL_PROVIDER", "fake") or "fake",
        openai_api_key=_env("OPENAI_API_KEY"),
        openai_text_model=_env("OPENAI_TEXT_MODEL", "gpt-4o") or "gpt-4o",
        openai_vision_model=_env("OPENAI_VISION_MODEL", "gpt-4o") or "gpt-4o",
        storage_root=data_root / "objects",
        render_runner=_env("RENDER_RUNNER", "docker") or "docker",
        render_image=_env("RENDER_IMAGE", "agentic-manim-runtime:local")
        or "agentic-manim-runtime:local",
        max_package_bytes=int(_env("MAX_PACKAGE_BYTES", str(10 * 1024 * 1024)) or 0),
        max_attempts=int(_env("MAX_ATTEMPTS", "3") or 3),
        critic_threshold=float(_env("CRITIC_THRESHOLD", "0.8") or 0.8),
    )


@lru_cache
def get_settings() -> Settings:
    return _load()

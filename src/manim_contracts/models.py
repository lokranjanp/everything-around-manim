from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utcnow() -> datetime:
    return datetime.now(UTC)


class GenerationStatus(StrEnum):
    QUEUED = "queued"
    PLANNING = "planning"
    DESIGNING = "designing"
    GENERATING = "generating"
    VALIDATING = "validating"
    RENDERING_PREVIEW = "rendering_preview"
    CRITIQUING = "critiquing"
    REPAIRING = "repairing"
    RENDERING_FINAL = "rendering_final"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = {
    GenerationStatus.COMPLETED,
    GenerationStatus.COMPLETED_WITH_WARNINGS,
    GenerationStatus.FAILED,
    GenerationStatus.CANCELLED,
}


class RenderStatus(StrEnum):
    QUEUED = "queued"
    VALIDATING = "validating"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RenderProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quality: Literal["preview", "final"] = "final"
    width: Annotated[int, Field(ge=320, le=1920)] = 1920
    height: Annotated[int, Field(ge=240, le=1080)] = 1080
    fps: Annotated[int, Field(ge=1, le=30)] = 30
    max_duration_seconds: Annotated[int, Field(ge=1, le=180)] = 180

    @model_validator(mode="after")
    def enforce_sixteen_by_nine(self) -> RenderProfile:
        if self.width * 9 != self.height * 16:
            raise ValueError("v1 supports only 16:9 output")
        return self


class AssetRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: UUID
    object_key: str = Field(min_length=1, max_length=512)
    filename: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    media_type: str = Field(min_length=3, max_length=100)
    size_bytes: int = Field(gt=0, le=100 * 1024 * 1024)

    @property
    def runtime_path(self) -> str:
        return f"assets/{self.asset_id}/{self.filename}"


class SceneManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    generation_id: UUID
    attempt: int = Field(ge=1, le=3)
    entrypoint_file: Literal["scene.py"] = "scene.py"
    scene_class: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    profile: RenderProfile
    outputs: list[Literal["mp4", "png"]] = ["mp4", "png"]
    assets: list[AssetRef] = []
    created_at: datetime = Field(default_factory=utcnow)


class GenerationOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duration_seconds: int = Field(default=60, ge=1, le=180)
    width: Literal[1920] = 1920
    height: Literal[1080] = 1080
    fps: int = Field(default=30, ge=1, le=30)


class GenerationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=3, max_length=20_000)
    asset_ids: list[UUID] = Field(default_factory=list, max_length=20)
    options: GenerationOptions = Field(default_factory=GenerationOptions)


class RevisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instruction: str = Field(min_length=3, max_length=10_000)


class Artifact(BaseModel):
    kind: Literal["preview", "mp4", "png", "contact_sheet", "source", "diagnostics"]
    object_key: str
    sha256: str | None = None
    size_bytes: int | None = None
    download_url: str | None = None


class GenerationRead(BaseModel):
    id: UUID
    parent_id: UUID | None = None
    prompt: str
    status: GenerationStatus
    attempt: int
    max_attempts: int
    critic_score: float | None = None
    warning: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    artifacts: list[Artifact] = []
    created_at: datetime
    updated_at: datetime


class GenerationEvent(BaseModel):
    id: int
    generation_id: UUID
    status: GenerationStatus
    message: str
    created_at: datetime


class UploadIntent(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=3, max_length=100)
    size_bytes: int = Field(gt=0, le=100 * 1024 * 1024)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class UploadIntentRead(BaseModel):
    asset_id: UUID
    upload_url: str
    object_key: str
    expires_in_seconds: int = 900


class UploadComplete(BaseModel):
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class StoryBeat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    objective: str
    duration_seconds: int = Field(ge=1, le=180)


class Storyboard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    learning_objective: str
    beats: list[StoryBeat] = Field(min_length=1, max_length=12)


class SceneDesign(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visual_style: str
    background_color: str
    choreography: list[str] = Field(min_length=1, max_length=30)
    asset_usage: list[str]


class GeneratedScene(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_class: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
    source: str = Field(min_length=20, max_length=250_000)


class CriticReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
    semantic_alignment: float = Field(ge=0, le=1)
    readability: float = Field(ge=0, le=1)
    composition: float = Field(ge=0, le=1)
    continuity: float = Field(ge=0, le=1)
    blocking_issues: list[str]
    repair_instructions: list[str]

    @property
    def aggregate_score(self) -> float:
        return (
            self.semantic_alignment + self.readability + self.composition + self.continuity
        ) / 4


class RenderJobCreate(BaseModel):
    package_key: str
    package_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    manifest: SceneManifest


class RenderJobRead(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    status: RenderStatus
    artifacts: list[Artifact] = []
    logs: str = ""
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, TypeVar
from uuid import UUID, uuid4

import msgspec


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


class StrictStruct(msgspec.Struct, forbid_unknown_fields=True):
    pass


class RenderProfile(StrictStruct):
    quality: Literal["preview", "final"] = "final"
    width: Annotated[int, msgspec.Meta(ge=320, le=1920)] = 1920
    height: Annotated[int, msgspec.Meta(ge=240, le=1080)] = 1080
    fps: Annotated[int, msgspec.Meta(ge=1, le=30)] = 30
    max_duration_seconds: Annotated[int, msgspec.Meta(ge=1, le=180)] = 180

    def __post_init__(self) -> None:
        if not 320 <= self.width <= 1920 or not 240 <= self.height <= 1080:
            raise ValueError("render dimensions are outside the supported range")
        if not 1 <= self.fps <= 30 or not 1 <= self.max_duration_seconds <= 180:
            raise ValueError("render timing is outside the supported range")
        if self.width * 9 != self.height * 16:
            raise ValueError("v1 supports only 16:9 output")


class AssetRef(StrictStruct):
    asset_id: UUID
    object_key: Annotated[str, msgspec.Meta(min_length=1, max_length=512)]
    filename: Annotated[str, msgspec.Meta(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")]
    sha256: Annotated[str, msgspec.Meta(pattern=r"^[a-f0-9]{64}$")]
    media_type: Annotated[str, msgspec.Meta(min_length=3, max_length=100)]
    size_bytes: Annotated[int, msgspec.Meta(ge=1, le=100 * 1024 * 1024)]

    @property
    def runtime_path(self) -> str:
        return f"assets/{self.asset_id}/{self.filename}"


class SceneManifest(StrictStruct):
    generation_id: UUID
    attempt: Annotated[int, msgspec.Meta(ge=1, le=3)]
    scene_class: Annotated[str, msgspec.Meta(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")]
    source_sha256: Annotated[str, msgspec.Meta(pattern=r"^[a-f0-9]{64}$")]
    profile: RenderProfile
    schema_version: Literal["1.0"] = "1.0"
    entrypoint_file: Literal["scene.py"] = "scene.py"
    outputs: list[Literal["mp4", "png"]] = msgspec.field(default_factory=lambda: ["mp4", "png"])
    assets: list[AssetRef] = msgspec.field(default_factory=list)
    created_at: datetime = msgspec.field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if self.schema_version != "1.0":
            raise ValueError("unsupported manifest schema version")
        if not 1 <= self.attempt <= 3:
            raise ValueError("attempt must be between 1 and 3")
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", self.scene_class) is None:
            raise ValueError("invalid scene class")
        if re.fullmatch(r"[a-f0-9]{64}", self.source_sha256) is None:
            raise ValueError("invalid source hash")


class GenerationOptions(StrictStruct):
    duration_seconds: Annotated[int, msgspec.Meta(ge=1, le=180)] = 60
    width: Literal[1920] = 1920
    height: Literal[1080] = 1080
    fps: Annotated[int, msgspec.Meta(ge=1, le=30)] = 30


class GenerationCreate(StrictStruct):
    prompt: Annotated[str, msgspec.Meta(min_length=3, max_length=20_000)]
    asset_ids: Annotated[list[UUID], msgspec.Meta(max_length=20)] = msgspec.field(default_factory=list)
    options: GenerationOptions = msgspec.field(default_factory=GenerationOptions)


class RevisionCreate(StrictStruct):
    instruction: Annotated[str, msgspec.Meta(min_length=3, max_length=10_000)]


class Artifact(StrictStruct):
    kind: Literal["preview", "mp4", "png", "contact_sheet", "source", "diagnostics"]
    object_key: str
    sha256: str | None = None
    size_bytes: int | None = None
    download_url: str | None = None


class GenerationRead(StrictStruct):
    id: UUID
    prompt: str
    status: GenerationStatus
    attempt: int
    max_attempts: int
    created_at: datetime
    updated_at: datetime
    parent_id: UUID | None = None
    critic_score: float | None = None
    warning: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    artifacts: list[Artifact] = msgspec.field(default_factory=list)


class GenerationEvent(StrictStruct):
    id: int
    generation_id: UUID
    status: GenerationStatus
    message: str
    created_at: datetime


class UploadIntent(StrictStruct):
    filename: Annotated[str, msgspec.Meta(min_length=1, max_length=255)]
    media_type: Annotated[str, msgspec.Meta(min_length=3, max_length=100)]
    size_bytes: Annotated[int, msgspec.Meta(ge=1, le=100 * 1024 * 1024)]
    sha256: Annotated[str, msgspec.Meta(pattern=r"^[a-f0-9]{64}$")]


class UploadIntentRead(StrictStruct):
    asset_id: UUID
    upload_url: str
    object_key: str
    expires_in_seconds: int = 900


class UploadComplete(StrictStruct):
    sha256: Annotated[str, msgspec.Meta(pattern=r"^[a-f0-9]{64}$")]


class StoryBeat(StrictStruct):
    title: str
    objective: str
    duration_seconds: Annotated[int, msgspec.Meta(ge=1, le=180)]


class Storyboard(StrictStruct):
    title: str
    learning_objective: str
    beats: Annotated[list[StoryBeat], msgspec.Meta(min_length=1, max_length=12)]


class SceneDesign(StrictStruct):
    visual_style: str
    background_color: str
    choreography: Annotated[list[str], msgspec.Meta(min_length=1, max_length=30)]
    asset_usage: list[str]


class GeneratedScene(StrictStruct):
    scene_class: Annotated[str, msgspec.Meta(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")]
    source: Annotated[str, msgspec.Meta(min_length=20, max_length=250_000)]


class CriticReport(StrictStruct):
    approved: bool
    semantic_alignment: Annotated[float, msgspec.Meta(ge=0, le=1)]
    readability: Annotated[float, msgspec.Meta(ge=0, le=1)]
    composition: Annotated[float, msgspec.Meta(ge=0, le=1)]
    continuity: Annotated[float, msgspec.Meta(ge=0, le=1)]
    blocking_issues: list[str]
    repair_instructions: list[str]

    @property
    def aggregate_score(self) -> float:
        return (
            self.semantic_alignment + self.readability + self.composition + self.continuity
        ) / 4


class RenderJobCreate(StrictStruct):
    package_key: str
    package_sha256: Annotated[str, msgspec.Meta(pattern=r"^[a-f0-9]{64}$")]
    manifest: SceneManifest


class RenderJobRead(StrictStruct):
    status: RenderStatus
    id: UUID = msgspec.field(default_factory=uuid4)
    artifacts: list[Artifact] = msgspec.field(default_factory=list)
    logs: str = ""
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime = msgspec.field(default_factory=utcnow)
    updated_at: datetime = msgspec.field(default_factory=utcnow)


T = TypeVar("T")


def encode_json(value: Any, *, indent: int | None = None) -> bytes:
    raw = msgspec.json.encode(value)
    return raw if indent is None else msgspec.json.format(raw, indent=indent)


def decode_json(data: str | bytes, schema: type[T]) -> T:
    return msgspec.json.decode(data.encode() if isinstance(data, str) else data, type=schema)


def convert(value: Any, schema: type[T]) -> T:
    return msgspec.convert(value, type=schema, strict=True)


def to_builtins(value: Any) -> Any:
    return msgspec.json.decode(msgspec.json.encode(value))


def json_schema(schema: type[Any]) -> dict[str, Any]:
    return msgspec.json.schema(schema)

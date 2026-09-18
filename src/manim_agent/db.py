from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from manim_contracts.models import GenerationStatus, utcnow

from .settings import get_settings


class Base(DeclarativeBase):
    pass


class Generation(Base):
    __tablename__ = "generations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("generations.id"), nullable=True)
    owner_key_hash: Mapped[str] = mapped_column(String(64), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255))
    prompt: Mapped[str] = mapped_column(Text)
    asset_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(40), default=GenerationStatus.QUEUED.value)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    critic_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    warning: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    workflow_data: Mapped[dict] = mapped_column(JSON, default=dict)
    artifacts: Mapped[list[dict]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    events: Mapped[list[Event]] = relationship(
        back_populates="generation", cascade="all, delete-orphan", order_by="Event.id"
    )

    @property
    def uuid(self) -> UUID:
        return UUID(self.id)


class Event(Base):
    __tablename__ = "generation_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    generation_id: Mapped[str] = mapped_column(ForeignKey("generations.id"), index=True)
    status: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    generation: Mapped[Generation] = relationship(back_populates="events")


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_key_hash: Mapped[str] = mapped_column(String(64), index=True)
    object_key: Mapped[str] = mapped_column(String(512), unique=True)
    filename: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    completed: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"

    owner_key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    generation_id: Mapped[str] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def _prepare_sqlite(url: str) -> None:
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///" )).parent.mkdir(parents=True, exist_ok=True)


settings = get_settings()
_prepare_sqlite(settings.database_url)
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def create_schema() -> None:
    Base.metadata.create_all(engine)


def get_session():
    with SessionLocal() as session:
        yield session


def add_event(session, generation: Generation, status: GenerationStatus, message: str) -> None:
    generation.status = status.value
    generation.updated_at = utcnow()
    session.add(Event(generation_id=generation.id, status=status.value, message=message))
    session.commit()

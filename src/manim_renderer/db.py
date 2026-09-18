from datetime import datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from manim_contracts.models import RenderStatus, utcnow

from .settings import get_render_settings


class Base(DeclarativeBase):
    pass


class RenderJob(Base):
    __tablename__ = "render_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    status: Mapped[str] = mapped_column(String(30), default=RenderStatus.QUEUED.value, index=True)
    package_key: Mapped[str] = mapped_column(String(512))
    package_sha256: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[dict] = mapped_column(JSON)
    artifacts: Mapped[list[dict]] = mapped_column(JSON, default=list)
    logs: Mapped[str] = mapped_column(Text, default="")
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


settings = get_render_settings()
if settings.render_database_url.startswith("sqlite:///"):
    Path(settings.render_database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
engine = create_engine(
    settings.render_database_url,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False}
    if settings.render_database_url.startswith("sqlite")
    else {},
)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def create_schema() -> None:
    Base.metadata.create_all(engine)


def get_session():
    with SessionLocal() as session:
        yield session

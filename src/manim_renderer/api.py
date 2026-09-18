from __future__ import annotations

import secrets
from uuid import UUID

from litestar import Litestar, Request, Response, get, post
from litestar.exceptions import HTTPException, ValidationException
from litestar.openapi.config import OpenAPIConfig
from litestar.openapi.plugins import SwaggerRenderPlugin
from litestar.params import FromPath
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from sqlalchemy import select

from manim_contracts.models import RenderJobCreate, RenderJobRead, RenderStatus, to_builtins, utcnow
from manim_contracts.telemetry import configure_telemetry

from .db import RenderJob, SessionLocal, create_schema
from .service import execute_render_job
from .settings import get_render_settings
from .tasks import execute_render_task

SUBMITTED = Counter("manim_render_jobs_submitted_total", "Render jobs submitted")


def _authorize(request: Request) -> None:
    expected = f"Bearer {get_render_settings().internal_render_token}"
    provided = request.headers.get("Authorization")
    if provided is None:
        raise HTTPException(status_code=422, detail="Authorization header is required")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="invalid internal token")


def _read(job: RenderJob) -> RenderJobRead:
    return RenderJobRead(id=UUID(job.id), status=RenderStatus(job.status), artifacts=job.artifacts or [],
                         logs=job.logs, error_code=job.error_code, error_message=job.error_message,
                         created_at=job.created_at, updated_at=job.updated_at)


@get("/health/live", tags=["operations"], sync_to_thread=False)
def live() -> dict[str, str]:
    return {"status": "ok"}


@get("/health/ready", tags=["operations"], sync_to_thread=False)
def ready() -> dict[str, str]:
    with SessionLocal() as session:
        session.execute(select(1))
    return {"status": "ready"}


@get("/metrics", include_in_schema=False, sync_to_thread=False)
def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@get("/openapi.json", include_in_schema=False, sync_to_thread=False)
def openapi_json() -> dict:
    return litestar_app.openapi_schema.to_schema()


@post("/internal/v1/render-jobs", status_code=202, sync_to_thread=False)
def create_render_job(data: RenderJobCreate, request: Request) -> RenderJobRead:
    _authorize(request)
    with SessionLocal() as session:
        existing = session.scalar(select(RenderJob).where(
            RenderJob.package_sha256 == data.package_sha256,
            RenderJob.status.in_([RenderStatus.QUEUED.value, RenderStatus.VALIDATING.value,
                                  RenderStatus.RUNNING.value, RenderStatus.COMPLETED.value])))
        if existing:
            return _read(existing)
        job = RenderJob(package_key=data.package_key, package_sha256=data.package_sha256,
                        manifest=to_builtins(data.manifest))
        session.add(job)
        session.commit()
        job_id = job.id
    SUBMITTED.inc()
    if get_render_settings().render_execution_mode == "celery":
        execute_render_task.delay(job_id)
    else:
        execute_render_job(job_id)
    with SessionLocal() as session:
        return _read(session.get(RenderJob, job_id))


@get("/internal/v1/render-jobs/{job_id:uuid}", sync_to_thread=False)
def get_render_job(job_id: FromPath[UUID], request: Request) -> RenderJobRead:
    _authorize(request)
    with SessionLocal() as session:
        job = session.get(RenderJob, str(job_id))
        if job is None:
            raise HTTPException(status_code=404, detail="render job not found")
        return _read(job)


@post("/internal/v1/render-jobs/{job_id:uuid}/cancel", sync_to_thread=False)
def cancel_render_job(job_id: FromPath[UUID], request: Request) -> RenderJobRead:
    _authorize(request)
    with SessionLocal() as session:
        job = session.get(RenderJob, str(job_id))
        if job is None:
            raise HTTPException(status_code=404, detail="render job not found")
        if job.status not in {RenderStatus.COMPLETED.value, RenderStatus.FAILED.value}:
            job.cancel_requested = True
            if job.status == RenderStatus.QUEUED.value:
                job.status = RenderStatus.CANCELLED.value
            job.updated_at = utcnow()
            session.commit()
        return _read(job)


def validation_handler(request: Request, exc: ValidationException) -> Response:
    return Response(content={"detail": getattr(exc, "extra", None) or str(exc)}, status_code=422)


def http_exception_handler(request: Request, exc: HTTPException) -> Response:
    return Response(content={"detail": exc.detail}, status_code=exc.status_code,
                    headers=exc.headers)


litestar_app = Litestar(
    route_handlers=[live, ready, metrics, openapi_json, create_render_job, get_render_job,
                    cancel_render_job],
    on_startup=[create_schema],
    exception_handlers={HTTPException: http_exception_handler,
                        ValidationException: validation_handler},
    openapi_config=OpenAPIConfig(title="Agentic Manim Render API", version="0.1.0",
                                 path="/docs", render_plugins=[SwaggerRenderPlugin()]),
)
app = configure_telemetry(litestar_app, "manim-render-api")

from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from sqlalchemy import select
from sqlalchemy.orm import Session

from manim_contracts.models import RenderJobCreate, RenderJobRead, RenderStatus, utcnow
from manim_contracts.telemetry import configure_telemetry

from .db import RenderJob, create_schema, get_session
from .service import execute_render_job
from .settings import get_render_settings
from .tasks import execute_render_task

SUBMITTED = Counter("manim_render_jobs_submitted_total", "Render jobs submitted")


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_schema()
    yield


app = FastAPI(title="Agentic Manim Render API", version="0.1.0", lifespan=lifespan)
configure_telemetry(app, "manim-render-api")


def require_internal_token(authorization: str = Header()) -> None:
    expected = f"Bearer {get_render_settings().internal_render_token}"
    if not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="invalid internal token")


def _read(job: RenderJob) -> RenderJobRead:
    return RenderJobRead(
        id=UUID(job.id), status=RenderStatus(job.status), artifacts=job.artifacts or [],
        logs=job.logs, error_code=job.error_code, error_message=job.error_message,
        created_at=job.created_at, updated_at=job.updated_at,
    )


@app.get("/health/live", tags=["operations"])
def live() -> dict:
    return {"status": "ok"}


@app.get("/health/ready", tags=["operations"])
def ready(session: Session = Depends(get_session)) -> dict:
    session.execute(select(1))
    return {"status": "ready"}


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post(
    "/internal/v1/render-jobs", response_model=RenderJobRead, status_code=202,
    dependencies=[Depends(require_internal_token)],
)
def create_render_job(
    body: RenderJobCreate,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
) -> RenderJobRead:
    existing = session.scalar(
        select(RenderJob).where(
            RenderJob.package_sha256 == body.package_sha256,
            RenderJob.status.in_([RenderStatus.QUEUED.value, RenderStatus.VALIDATING.value,
                                  RenderStatus.RUNNING.value, RenderStatus.COMPLETED.value]),
        )
    )
    if existing:
        return _read(existing)
    job = RenderJob(
        package_key=body.package_key,
        package_sha256=body.package_sha256,
        manifest=body.manifest.model_dump(mode="json"),
    )
    session.add(job)
    session.commit()
    SUBMITTED.inc()
    if get_render_settings().render_execution_mode == "celery":
        execute_render_task.delay(job.id)
    else:
        background.add_task(execute_render_job, job.id)
    return _read(job)


@app.get(
    "/internal/v1/render-jobs/{job_id}", response_model=RenderJobRead,
    dependencies=[Depends(require_internal_token)],
)
def get_render_job(job_id: UUID, session: Session = Depends(get_session)) -> RenderJobRead:
    job = session.get(RenderJob, str(job_id))
    if job is None:
        raise HTTPException(status_code=404, detail="render job not found")
    return _read(job)


@app.post(
    "/internal/v1/render-jobs/{job_id}/cancel", response_model=RenderJobRead,
    dependencies=[Depends(require_internal_token)],
)
def cancel_render_job(job_id: UUID, session: Session = Depends(get_session)) -> RenderJobRead:
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

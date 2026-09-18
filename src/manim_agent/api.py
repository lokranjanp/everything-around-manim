from __future__ import annotations

import asyncio
import hashlib
import re
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
    Response,
)
from fastapi.responses import StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from sqlalchemy import select
from sqlalchemy.orm import Session

from manim_contracts.models import (
    Artifact,
    GenerationCreate,
    GenerationEvent,
    GenerationRead,
    GenerationStatus,
    RevisionCreate,
    UploadComplete,
    UploadIntent,
    UploadIntentRead,
)
from manim_contracts.telemetry import configure_telemetry

from .auth import require_api_key, require_idempotency_key
from .db import (
    Asset,
    Event,
    Generation,
    IdempotencyRecord,
    SessionLocal,
    add_event,
    create_schema,
    get_session,
)
from .settings import get_settings
from .storage import LocalObjectStore, get_object_store, sha256_bytes
from .tasks import run_generation_task
from .workflow import run_generation

CREATED = Counter("manim_generations_created_total", "Generations created")


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_schema()
    yield


app = FastAPI(title="Agentic Manim Control API", version="0.1.0", lifespan=lifespan)
configure_telemetry(app, "manim-control-api")


def _generation_read(generation: Generation, with_urls: bool = False) -> GenerationRead:
    store = get_object_store()
    artifacts = []
    for raw in generation.artifacts or []:
        artifact = Artifact.model_validate(raw)
        if with_urls:
            artifact.download_url = store.presigned_get(artifact.object_key)
        artifacts.append(artifact)
    return GenerationRead(
        id=UUID(generation.id),
        parent_id=UUID(generation.parent_id) if generation.parent_id else None,
        prompt=generation.prompt,
        status=GenerationStatus(generation.status),
        attempt=generation.attempt,
        max_attempts=generation.max_attempts,
        critic_score=generation.critic_score,
        warning=generation.warning,
        error_code=generation.error_code,
        error_message=generation.error_message,
        artifacts=artifacts,
        created_at=generation.created_at,
        updated_at=generation.updated_at,
    )


def _owned_generation(session: Session, generation_id: UUID, owner_hash: str) -> Generation:
    generation = session.scalar(
        select(Generation).where(
            Generation.id == str(generation_id), Generation.owner_key_hash == owner_hash
        )
    )
    if generation is None:
        raise HTTPException(status_code=404, detail="generation not found")
    return generation


def _dispatch(generation_id: str, background: BackgroundTasks) -> None:
    settings = get_settings()
    if settings.workflow_mode == "celery":
        run_generation_task.delay(generation_id)
    else:
        background.add_task(run_generation, generation_id)


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


@app.post("/v1/assets/upload-intents", response_model=UploadIntentRead, status_code=201)
def create_upload_intent(
    body: UploadIntent,
    owner_hash: str = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> UploadIntentRead:
    if not (
        body.media_type.startswith("image/")
        or body.media_type.startswith("font/")
        or body.media_type in {"text/csv", "application/json"}
    ):
        raise HTTPException(status_code=415, detail="asset media type is not supported")
    asset_id = uuid4()
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", body.filename).lstrip("._-")
    safe_name = (safe_name or "asset")[:255]
    key = f"assets/{owner_hash[:12]}/{asset_id}/{safe_name}"
    asset = Asset(
        id=str(asset_id), owner_key_hash=owner_hash, object_key=key, filename=safe_name,
        media_type=body.media_type, size_bytes=body.size_bytes, sha256=body.sha256,
    )
    session.add(asset)
    session.commit()
    return UploadIntentRead(
        asset_id=asset_id,
        upload_url=get_object_store().presigned_put(key, body.media_type),
        object_key=key,
    )


@app.put("/v1/dev-uploads/{object_key:path}", include_in_schema=False, status_code=204)
async def dev_upload(object_key: str, request: Request) -> Response:
    store = get_object_store()
    if not isinstance(store, LocalObjectStore):
        raise HTTPException(status_code=404)
    data = await request.body()
    if len(data) > 100 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="asset too large")
    store.put_bytes(object_key, data, request.headers.get("content-type", "application/octet-stream"))
    return Response(status_code=204)


@app.get("/v1/dev-objects/{object_key:path}", include_in_schema=False)
def dev_download(object_key: str) -> Response:
    store = get_object_store()
    if not isinstance(store, LocalObjectStore):
        raise HTTPException(status_code=404)
    try:
        data = store.get_bytes(object_key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404) from exc
    return Response(data, media_type="application/octet-stream")


@app.post("/v1/assets/{asset_id}/complete", status_code=204)
def complete_upload(
    asset_id: UUID,
    body: UploadComplete,
    owner_hash: str = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> Response:
    asset = session.scalar(
        select(Asset).where(Asset.id == str(asset_id), Asset.owner_key_hash == owner_hash)
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="asset not found")
    store = get_object_store()
    if not store.exists(asset.object_key):
        raise HTTPException(status_code=409, detail="asset has not been uploaded")
    raw = store.get_bytes(asset.object_key)
    if len(raw) != asset.size_bytes or sha256_bytes(raw) != body.sha256 or body.sha256 != asset.sha256:
        raise HTTPException(status_code=422, detail="asset size or checksum mismatch")
    asset.completed = True
    session.commit()
    return Response(status_code=204)


@app.post("/v1/generations", response_model=GenerationRead, status_code=202)
def create_generation(
    body: GenerationCreate,
    background: BackgroundTasks,
    owner_hash: str = Depends(require_api_key),
    idempotency_key: str = Depends(require_idempotency_key),
    session: Session = Depends(get_session),
) -> GenerationRead:
    request_hash = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
    existing = session.get(IdempotencyRecord, (owner_hash, idempotency_key))
    if existing:
        if existing.request_hash != request_hash:
            raise HTTPException(status_code=409, detail="idempotency key reused with different body")
        generation = session.get(Generation, existing.generation_id)
        return _generation_read(generation)

    if body.asset_ids:
        count = len(
            session.scalars(
                select(Asset).where(
                    Asset.id.in_([str(value) for value in body.asset_ids]),
                    Asset.owner_key_hash == owner_hash,
                    Asset.completed.is_(True),
                )
            ).all()
        )
        if count != len(set(body.asset_ids)):
            raise HTTPException(status_code=422, detail="one or more assets are missing or incomplete")

    generation = Generation(
        owner_key_hash=owner_hash,
        idempotency_key=idempotency_key,
        prompt=body.prompt,
        asset_ids=[str(value) for value in body.asset_ids],
        options=body.options.model_dump(mode="json"),
        max_attempts=get_settings().max_attempts,
    )
    session.add(generation)
    session.flush()
    session.add(
        IdempotencyRecord(
            owner_key_hash=owner_hash, key=idempotency_key,
            request_hash=request_hash, generation_id=generation.id,
        )
    )
    session.add(
        Event(generation_id=generation.id, status=GenerationStatus.QUEUED.value, message="Generation queued")
    )
    session.commit()
    CREATED.inc()
    _dispatch(generation.id, background)
    return _generation_read(generation)


@app.get("/v1/generations/{generation_id}", response_model=GenerationRead)
def get_generation(
    generation_id: UUID,
    owner_hash: str = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> GenerationRead:
    return _generation_read(_owned_generation(session, generation_id, owner_hash))


@app.get("/v1/generations/{generation_id}/artifacts", response_model=list[Artifact])
def get_artifacts(
    generation_id: UUID,
    owner_hash: str = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> list[Artifact]:
    return _generation_read(_owned_generation(session, generation_id, owner_hash), with_urls=True).artifacts


@app.post("/v1/generations/{generation_id}/cancel", response_model=GenerationRead)
def cancel_generation(
    generation_id: UUID,
    owner_hash: str = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> GenerationRead:
    generation = _owned_generation(session, generation_id, owner_hash)
    if GenerationStatus(generation.status) not in {
        GenerationStatus.COMPLETED,
        GenerationStatus.COMPLETED_WITH_WARNINGS,
        GenerationStatus.FAILED,
    }:
        add_event(session, generation, GenerationStatus.CANCELLED, "Cancellation requested")
    return _generation_read(generation)


@app.post("/v1/generations/{generation_id}/revisions", response_model=GenerationRead, status_code=202)
def create_revision(
    generation_id: UUID,
    body: RevisionCreate,
    background: BackgroundTasks,
    owner_hash: str = Depends(require_api_key),
    idempotency_key: str = Depends(require_idempotency_key),
    session: Session = Depends(get_session),
) -> GenerationRead:
    parent = _owned_generation(session, generation_id, owner_hash)
    if GenerationStatus(parent.status) not in {
        GenerationStatus.COMPLETED,
        GenerationStatus.COMPLETED_WITH_WARNINGS,
    }:
        raise HTTPException(status_code=409, detail="only completed generations can be revised")
    request = GenerationCreate(
        prompt=f"Original request: {parent.prompt}\nRevision: {body.instruction}",
        asset_ids=[UUID(value) for value in parent.asset_ids],
        options=parent.options,
    )
    request_hash = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
    existing = session.get(IdempotencyRecord, (owner_hash, idempotency_key))
    if existing:
        return _generation_read(session.get(Generation, existing.generation_id))
    generation = Generation(
        parent_id=parent.id, owner_key_hash=owner_hash, idempotency_key=idempotency_key,
        prompt=request.prompt, asset_ids=parent.asset_ids, options=parent.options,
        max_attempts=get_settings().max_attempts,
        workflow_data={"parent_context": parent.workflow_data},
    )
    session.add(generation)
    session.flush()
    session.add(IdempotencyRecord(
        owner_key_hash=owner_hash, key=idempotency_key,
        request_hash=request_hash, generation_id=generation.id,
    ))
    session.add(Event(
        generation_id=generation.id, status=GenerationStatus.QUEUED.value,
        message=f"Revision queued from {parent.id}",
    ))
    session.commit()
    _dispatch(generation.id, background)
    return _generation_read(generation)


@app.get("/v1/generations/{generation_id}/events")
async def stream_events(
    generation_id: UUID,
    request: Request,
    owner_hash: str = Depends(require_api_key),
    last_event_id: int = Header(default=0, alias="Last-Event-ID"),
) -> StreamingResponse:
    with SessionLocal() as session:
        _owned_generation(session, generation_id, owner_hash)

    async def event_stream():
        cursor = last_event_id
        while not await request.is_disconnected():
            with SessionLocal() as session:
                events = session.scalars(
                    select(Event).where(
                        Event.generation_id == str(generation_id), Event.id > cursor
                    ).order_by(Event.id)
                ).all()
                for event in events:
                    cursor = event.id
                    payload = GenerationEvent(
                        id=event.id, generation_id=generation_id,
                        status=GenerationStatus(event.status), message=event.message,
                        created_at=event.created_at,
                    ).model_dump_json()
                    yield f"id: {event.id}\nevent: generation.status\ndata: {payload}\n\n"
                generation = session.get(Generation, str(generation_id))
                if generation and GenerationStatus(generation.status) in {
                    GenerationStatus.COMPLETED, GenerationStatus.COMPLETED_WITH_WARNINGS,
                    GenerationStatus.FAILED, GenerationStatus.CANCELLED,
                } and not events:
                    return
            yield ": keep-alive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(event_stream(), media_type="text/event-stream")

from __future__ import annotations

import asyncio
import hashlib
import re
from uuid import UUID, uuid4

from litestar import Litestar, Request, Response, get, post, put
from litestar.exceptions import HTTPException, ValidationException
from litestar.openapi.config import OpenAPIConfig
from litestar.openapi.plugins import SwaggerRenderPlugin
from litestar.params import FromPath
from litestar.response import Stream
from sqlalchemy import select

from manim_agent.auth import AuthenticationError, authenticate_api_key, validate_idempotency_key
from manim_agent.db import (
    Asset,
    Event,
    Generation,
    IdempotencyRecord,
    SessionLocal,
    add_event,
    create_schema,
)
from manim_agent.settings import get_settings
from manim_agent.storage import LocalObjectStore, get_object_store, sha256_bytes
from manim_contracts.models import (
    Artifact,
    GenerationCreate,
    GenerationEvent,
    GenerationOptions,
    GenerationRead,
    GenerationStatus,
    RevisionCreate,
    UploadComplete,
    UploadIntent,
    UploadIntentRead,
    convert,
    encode_json,
    to_builtins,
)

from .queue import (
    dispatch_generation,
    start_generation_queue,
    stop_generation_queue,
)


def _headers(request: Request, *, idempotent: bool = False) -> tuple[str, str | None]:
    try:
        owner = authenticate_api_key(request.headers.get("X-API-Key"))
    except AuthenticationError as exc:
        status = 422 if request.headers.get("X-API-Key") is None else 401
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    key = None
    if idempotent:
        try:
            key = validate_idempotency_key(request.headers.get("Idempotency-Key"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return owner, key


def _generation_read(generation: Generation, with_urls: bool = False) -> GenerationRead:
    artifacts = []
    for raw in generation.artifacts or []:
        artifact = convert(raw, Artifact)
        if with_urls:
            artifact.download_url = get_object_store().presigned_get(artifact.object_key)
        artifacts.append(artifact)
    return GenerationRead(
        id=UUID(generation.id), parent_id=UUID(generation.parent_id) if generation.parent_id else None,
        prompt=generation.prompt, status=GenerationStatus(generation.status), attempt=generation.attempt,
        max_attempts=generation.max_attempts, critic_score=generation.critic_score,
        warning=generation.warning, error_code=generation.error_code,
        error_message=generation.error_message, artifacts=artifacts,
        created_at=generation.created_at, updated_at=generation.updated_at,
    )


def _owned_generation(session, generation_id: UUID, owner_hash: str) -> Generation:
    generation = session.scalar(select(Generation).where(
        Generation.id == str(generation_id), Generation.owner_key_hash == owner_hash))
    if generation is None:
        raise HTTPException(status_code=404, detail="generation not found")
    return generation


def _dispatch(generation_id: str) -> None:
    dispatch_generation(generation_id)


@get("/health/live", tags=["operations"], sync_to_thread=False)
def live() -> dict[str, str]:
    return {"status": "ok"}


@get("/health/ready", tags=["operations"], sync_to_thread=False)
def ready() -> dict[str, str]:
    with SessionLocal() as session:
        session.execute(select(1))
    return {"status": "ready"}


@get("/openapi.json", include_in_schema=False, sync_to_thread=False)
def openapi_json() -> dict:
    return litestar_app.openapi_schema.to_schema()


@post("/v1/assets/upload-intents", status_code=201, sync_to_thread=False)
def create_upload_intent(data: UploadIntent, request: Request) -> UploadIntentRead:
    owner_hash, _ = _headers(request)
    if not (data.media_type.startswith("image/") or data.media_type.startswith("font/")
            or data.media_type in {"text/csv", "application/json"}):
        raise HTTPException(status_code=415, detail="asset media type is not supported")
    asset_id = uuid4()
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", data.filename).lstrip("._-")
    safe_name = (safe_name or "asset")[:255]
    key = f"assets/{owner_hash[:12]}/{asset_id}/{safe_name}"
    with SessionLocal() as session:
        session.add(Asset(id=str(asset_id), owner_key_hash=owner_hash, object_key=key,
                          filename=safe_name, media_type=data.media_type,
                          size_bytes=data.size_bytes, sha256=data.sha256))
        session.commit()
    return UploadIntentRead(asset_id=asset_id, upload_url=get_object_store().presigned_put(key, data.media_type),
                            object_key=key)


@put("/v1/dev-uploads/{object_key:path}", status_code=204)
async def dev_upload(object_key: FromPath[str], request: Request) -> None:
    store = get_object_store()
    if not isinstance(store, LocalObjectStore):
        raise HTTPException(status_code=404)
    data = await request.body()
    if len(data) > 100 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="asset too large")
    store.put_bytes(object_key.lstrip("/"), data,
                    request.headers.get("content-type", "application/octet-stream"))
    return None


@get("/v1/dev-objects/{object_key:path}", include_in_schema=False, sync_to_thread=False)
def dev_download(object_key: FromPath[str]) -> Response:
    store = get_object_store()
    if not isinstance(store, LocalObjectStore):
        raise HTTPException(status_code=404)
    try:
        data = store.get_bytes(object_key.lstrip("/"))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404) from exc
    return Response(content=data, media_type="application/octet-stream")


@post("/v1/assets/{asset_id:uuid}/complete", status_code=204, sync_to_thread=False)
def complete_upload(asset_id: FromPath[UUID], data: UploadComplete, request: Request) -> None:
    owner_hash, _ = _headers(request)
    with SessionLocal() as session:
        asset = session.scalar(select(Asset).where(
            Asset.id == str(asset_id), Asset.owner_key_hash == owner_hash))
        if asset is None:
            raise HTTPException(status_code=404, detail="asset not found")
        store = get_object_store()
        if not store.exists(asset.object_key):
            raise HTTPException(status_code=409, detail="asset has not been uploaded")
        raw = store.get_bytes(asset.object_key)
        if len(raw) != asset.size_bytes or sha256_bytes(raw) != data.sha256 or data.sha256 != asset.sha256:
            raise HTTPException(status_code=422, detail="asset size or checksum mismatch")
        asset.completed = True
        session.commit()
    return None


@post("/v1/generations", status_code=202, sync_to_thread=False)
def create_generation(data: GenerationCreate, request: Request) -> GenerationRead:
    owner_hash, idempotency_key = _headers(request, idempotent=True)
    assert idempotency_key is not None
    request_hash = hashlib.sha256(encode_json(data)).hexdigest()
    with SessionLocal() as session:
        existing = session.get(IdempotencyRecord, (owner_hash, idempotency_key))
        if existing:
            if existing.request_hash != request_hash:
                raise HTTPException(status_code=409, detail="idempotency key reused with different body")
            return _generation_read(session.get(Generation, existing.generation_id))
        if data.asset_ids:
            found = session.scalars(select(Asset).where(
                Asset.id.in_([str(value) for value in data.asset_ids]),
                Asset.owner_key_hash == owner_hash, Asset.completed.is_(True))).all()
            if len(found) != len(set(data.asset_ids)):
                raise HTTPException(status_code=422, detail="one or more assets are missing or incomplete")
        generation = Generation(owner_key_hash=owner_hash, idempotency_key=idempotency_key,
                                prompt=data.prompt, asset_ids=[str(value) for value in data.asset_ids],
                                options=to_builtins(data.options), max_attempts=get_settings().max_attempts)
        session.add(generation)
        session.flush()
        session.add(IdempotencyRecord(owner_key_hash=owner_hash, key=idempotency_key,
                                      request_hash=request_hash, generation_id=generation.id))
        session.add(Event(generation_id=generation.id, status=GenerationStatus.QUEUED.value,
                          message="Generation queued", event_key="queued"))
        session.commit()
        result = _generation_read(generation)
    _dispatch(str(result.id))
    return result


@get("/v1/generations/{generation_id:uuid}", sync_to_thread=False)
def get_generation(generation_id: FromPath[UUID], request: Request) -> GenerationRead:
    owner_hash, _ = _headers(request)
    with SessionLocal() as session:
        return _generation_read(_owned_generation(session, generation_id, owner_hash))


@get("/v1/generations/{generation_id:uuid}/artifacts", sync_to_thread=False)
def get_artifacts(generation_id: FromPath[UUID], request: Request) -> list[Artifact]:
    owner_hash, _ = _headers(request)
    with SessionLocal() as session:
        return _generation_read(_owned_generation(session, generation_id, owner_hash), True).artifacts


@post("/v1/generations/{generation_id:uuid}/cancel", sync_to_thread=False)
def cancel_generation(generation_id: FromPath[UUID], request: Request) -> GenerationRead:
    owner_hash, _ = _headers(request)
    with SessionLocal() as session:
        generation = _owned_generation(session, generation_id, owner_hash)
        if GenerationStatus(generation.status) not in {
            GenerationStatus.COMPLETED, GenerationStatus.COMPLETED_WITH_WARNINGS, GenerationStatus.FAILED}:
            add_event(session, generation, GenerationStatus.CANCELLED, "Cancellation requested", "cancel")
        return _generation_read(generation)


@post("/v1/generations/{generation_id:uuid}/revisions", status_code=202, sync_to_thread=False)
def create_revision(generation_id: FromPath[UUID], data: RevisionCreate, request: Request) -> GenerationRead:
    owner_hash, idempotency_key = _headers(request, idempotent=True)
    assert idempotency_key is not None
    with SessionLocal() as session:
        parent = _owned_generation(session, generation_id, owner_hash)
        if GenerationStatus(parent.status) not in {GenerationStatus.COMPLETED,
                                                   GenerationStatus.COMPLETED_WITH_WARNINGS}:
            raise HTTPException(status_code=409, detail="only completed generations can be revised")
        create = GenerationCreate(prompt=f"Original request: {parent.prompt}\nRevision: {data.instruction}",
                                  asset_ids=[UUID(value) for value in parent.asset_ids],
                                  options=convert(parent.options, GenerationOptions))
        request_hash = hashlib.sha256(encode_json(create)).hexdigest()
        existing = session.get(IdempotencyRecord, (owner_hash, idempotency_key))
        if existing:
            return _generation_read(session.get(Generation, existing.generation_id))
        generation = Generation(parent_id=parent.id, owner_key_hash=owner_hash,
                                idempotency_key=idempotency_key, prompt=create.prompt,
                                asset_ids=parent.asset_ids, options=parent.options,
                                max_attempts=get_settings().max_attempts,
                                workflow_data={"parent_context": parent.workflow_data})
        session.add(generation)
        session.flush()
        session.add(IdempotencyRecord(owner_key_hash=owner_hash, key=idempotency_key,
                                      request_hash=request_hash, generation_id=generation.id))
        session.add(Event(generation_id=generation.id, status=GenerationStatus.QUEUED.value,
                          message=f"Revision queued from {parent.id}", event_key="queued"))
        session.commit()
        result = _generation_read(generation)
    _dispatch(str(result.id))
    return result


@get("/v1/generations/{generation_id:uuid}/events")
async def stream_events(generation_id: FromPath[UUID], request: Request) -> Stream:
    owner_hash, _ = _headers(request)
    with SessionLocal() as session:
        _owned_generation(session, generation_id, owner_hash)
    try:
        cursor = int(request.headers.get("Last-Event-ID", "0"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Last-Event-ID must be an integer") from exc

    async def event_stream():
        nonlocal cursor
        while True:
            if await request.is_disconnected():
                return
            with SessionLocal() as session:
                events = session.scalars(select(Event).where(
                    Event.generation_id == str(generation_id), Event.id > cursor).order_by(Event.id)).all()
                for event in events:
                    cursor = event.id
                    payload = encode_json(GenerationEvent(
                        id=event.id, generation_id=generation_id, status=GenerationStatus(event.status),
                        message=event.message, created_at=event.created_at)).decode()
                    yield f"id: {event.id}\nevent: generation.status\ndata: {payload}\n\n"
                generation = session.get(Generation, str(generation_id))
                if generation and GenerationStatus(generation.status) in {
                    GenerationStatus.COMPLETED, GenerationStatus.COMPLETED_WITH_WARNINGS,
                    GenerationStatus.FAILED, GenerationStatus.CANCELLED} and not events:
                    return
            yield ": keep-alive\n\n"
            await asyncio.sleep(1)

    return Stream(event_stream(), media_type="text/event-stream")


def validation_handler(request: Request, exc: ValidationException) -> Response:
    detail = getattr(exc, "extra", None) or str(exc)
    return Response(content={"detail": detail}, status_code=422)


def http_exception_handler(request: Request, exc: HTTPException) -> Response:
    return Response(content={"detail": exc.detail}, status_code=exc.status_code,
                    headers=exc.headers)


litestar_app = Litestar(
    route_handlers=[live, ready, openapi_json, create_upload_intent, dev_upload, dev_download,
                    complete_upload, create_generation, get_generation, get_artifacts,
                    cancel_generation, create_revision, stream_events],
    on_startup=[create_schema, start_generation_queue],
    on_shutdown=[stop_generation_queue],
    exception_handlers={HTTPException: http_exception_handler,
                        ValidationException: validation_handler},
    openapi_config=OpenAPIConfig(title="Agentic Manim Control API", version="0.1.0",
                                 path="/docs", render_plugins=[SwaggerRenderPlugin()]),
)
app = litestar_app

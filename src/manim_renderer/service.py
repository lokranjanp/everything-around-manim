from __future__ import annotations

import hashlib

from manim_contracts.models import (
    Artifact,
    RenderStatus,
    SceneManifest,
    convert,
    to_builtins,
    utcnow,
)

from .db import RenderJob, SessionLocal
from .runner import RenderCancelled, get_runner
from .settings import get_render_settings
from .storage import get_render_store
from .validator import PackageValidationError, validate_package


def execute_render_job(job_id: str) -> None:
    settings = get_render_settings()
    store = get_render_store(settings)
    with SessionLocal() as session:
        job = session.get(RenderJob, job_id)
        if job is None or job.cancel_requested:
            if job:
                job.status = RenderStatus.CANCELLED.value
                session.commit()
            return
        job.status = RenderStatus.VALIDATING.value
        session.commit()
        try:
            raw = store.get_bytes(job.package_key)
            manifest = convert(job.manifest, SceneManifest)
            validated = validate_package(
                raw, manifest, job.package_sha256, settings.max_package_bytes
            )
            assets: dict[str, bytes] = {}
            for asset in manifest.assets:
                if not (
                    asset.media_type.startswith("image/")
                    or asset.media_type.startswith("font/")
                    or asset.media_type in {"text/csv", "application/json"}
                ):
                    raise PackageValidationError(
                        f"asset media type is not allowed: {asset.media_type}"
                    )
                data = store.get_bytes(asset.object_key)
                if len(data) != asset.size_bytes:
                    raise PackageValidationError(f"asset size mismatch: {asset.asset_id}")
                if hashlib.sha256(data).hexdigest() != asset.sha256:
                    raise PackageValidationError(f"asset checksum mismatch: {asset.asset_id}")
                assets[asset.runtime_path] = data
            session.refresh(job)
            if job.cancel_requested:
                job.status = RenderStatus.CANCELLED.value
                session.commit()
                return
            job.status = RenderStatus.RUNNING.value
            session.commit()
            def cancelled() -> bool:
                with SessionLocal() as cancel_session:
                    current = cancel_session.get(RenderJob, job_id)
                    return current is None or current.cancel_requested

            result = get_runner(settings).run(
                validated.source, validated.manifest, assets, cancel_check=cancelled
            )
            session.refresh(job)
            if job.cancel_requested:
                job.status = RenderStatus.CANCELLED.value
                session.commit()
                return
            prefix = (
                f"generations/{manifest.generation_id}/attempts/{manifest.attempt}/"
                f"{manifest.profile.quality}"
            )
            outputs = [
                ("mp4", f"{prefix}/final.mp4", result.mp4, "video/mp4"),
                ("png", f"{prefix}/final.png", result.png, "image/png"),
                ("contact_sheet", f"{prefix}/contact-sheet.png", result.contact_sheet, "image/png"),
            ]
            artifacts: list[dict] = []
            for kind, key, data, content_type in outputs:
                store.put_bytes(key, data, content_type)
                artifacts.append(
                    Artifact(
                        kind=kind,
                        object_key=key,
                        sha256=hashlib.sha256(data).hexdigest(),
                        size_bytes=len(data),
                    )
                )
            artifacts = [to_builtins(artifact) for artifact in artifacts]
            job.artifacts = artifacts
            job.logs = result.logs
            job.status = RenderStatus.COMPLETED.value
            job.updated_at = utcnow()
            session.commit()
        except RenderCancelled:
            session.rollback()
            job = session.get(RenderJob, job_id)
            job.status = RenderStatus.CANCELLED.value
            job.error_code = None
            job.error_message = None
            job.updated_at = utcnow()
            session.commit()
        except Exception as exc:
            session.rollback()
            job = session.get(RenderJob, job_id)
            job.status = RenderStatus.FAILED.value
            job.error_code = "PACKAGE_REJECTED" if isinstance(exc, PackageValidationError) else type(exc).__name__
            job.error_message = str(exc)[:4000]
            job.updated_at = utcnow()
            session.commit()

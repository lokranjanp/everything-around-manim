from __future__ import annotations

import hashlib
from collections.abc import Callable
from uuid import uuid4

from manim_agent.settings import Settings, get_settings
from manim_agent.storage import ObjectStore, get_object_store
from manim_contracts.models import (
    Artifact,
    RenderJobCreate,
    RenderJobRead,
    RenderStatus,
)

from .runner import RenderCancelled, Runner, get_runner
from .validator import PackageValidationError, validate_package


class LocalRenderService:
    def __init__(
        self,
        settings: Settings | None = None,
        store: ObjectStore | None = None,
        runner: Runner | None = None,
    ):
        self.settings = settings or get_settings()
        self.store = store or get_object_store(self.settings)
        self.runner = runner or get_runner(self.settings)

    def render(
        self,
        request: RenderJobCreate,
        should_cancel: Callable[[], bool] | None = None,
    ) -> RenderJobRead:
        job_id = uuid4()
        try:
            if should_cancel and should_cancel():
                raise RenderCancelled("render cancelled")
            raw = self.store.get_bytes(request.package_key)
            manifest = request.manifest
            validated = validate_package(
                raw, manifest, request.package_sha256, self.settings.max_package_bytes
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
                data = self.store.get_bytes(asset.object_key)
                if len(data) != asset.size_bytes:
                    raise PackageValidationError(f"asset size mismatch: {asset.asset_id}")
                if hashlib.sha256(data).hexdigest() != asset.sha256:
                    raise PackageValidationError(f"asset checksum mismatch: {asset.asset_id}")
                assets[asset.runtime_path] = data
            if should_cancel and should_cancel():
                raise RenderCancelled("render cancelled")
            result = self.runner.run(
                validated.source, validated.manifest, assets, cancel_check=should_cancel
            )
            prefix = (
                f"generations/{manifest.generation_id}/attempts/{manifest.attempt}/"
                f"{manifest.profile.quality}"
            )
            outputs = [
                ("mp4", f"{prefix}/final.mp4", result.mp4, "video/mp4"),
                ("png", f"{prefix}/final.png", result.png, "image/png"),
                (
                    "contact_sheet",
                    f"{prefix}/contact-sheet.png",
                    result.contact_sheet,
                    "image/png",
                ),
            ]
            artifacts = []
            for kind, key, data, content_type in outputs:
                self.store.put_bytes(key, data, content_type)
                artifacts.append(
                    Artifact(
                        kind=kind,
                        object_key=key,
                        sha256=hashlib.sha256(data).hexdigest(),
                        size_bytes=len(data),
                    )
                )
            return RenderJobRead(
                id=job_id,
                status=RenderStatus.COMPLETED,
                artifacts=artifacts,
                logs=result.logs,
            )
        except RenderCancelled:
            return RenderJobRead(id=job_id, status=RenderStatus.CANCELLED)
        except Exception as exc:
            error_code = (
                "PACKAGE_REJECTED" if isinstance(exc, PackageValidationError) else type(exc).__name__
            )
            return RenderJobRead(
                id=job_id,
                status=RenderStatus.FAILED,
                error_code=error_code,
                error_message=str(exc)[:4000],
            )

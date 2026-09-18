from __future__ import annotations

import time
from collections.abc import Callable
from uuid import UUID

import httpx

from manim_contracts.models import (
    RenderJobCreate,
    RenderJobRead,
    RenderStatus,
    convert,
    to_builtins,
)

from .settings import Settings, get_settings


class RenderClient:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.headers = {"Authorization": f"Bearer {self.settings.internal_render_token}"}

    def submit_and_wait(
        self,
        request: RenderJobCreate,
        timeout_seconds: int,
        should_cancel: Callable[[], bool] | None = None,
    ) -> RenderJobRead:
        with httpx.Client(base_url=self.settings.render_service_url, timeout=30) as client:
            response = client.post(
                "/internal/v1/render-jobs", headers=self.headers, json=to_builtins(request)
            )
            response.raise_for_status()
            job = convert(response.json(), RenderJobRead)
            deadline = time.monotonic() + timeout_seconds
            while job.status not in {
                RenderStatus.COMPLETED,
                RenderStatus.FAILED,
                RenderStatus.CANCELLED,
            }:
                if should_cancel and should_cancel():
                    client.post(
                        f"/internal/v1/render-jobs/{job.id}/cancel", headers=self.headers
                    ).raise_for_status()
                if time.monotonic() >= deadline:
                    client.post(f"/internal/v1/render-jobs/{job.id}/cancel", headers=self.headers)
                    raise TimeoutError(f"render {job.id} exceeded {timeout_seconds}s")
                time.sleep(1)
                response = client.get(f"/internal/v1/render-jobs/{job.id}", headers=self.headers)
                response.raise_for_status()
                job = convert(response.json(), RenderJobRead)
            return job

    def cancel(self, job_id: UUID) -> None:
        with httpx.Client(base_url=self.settings.render_service_url, timeout=10) as client:
            client.post(f"/internal/v1/render-jobs/{job_id}/cancel", headers=self.headers)

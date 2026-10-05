from collections.abc import Callable

from manim_contracts.models import (
    RenderJobCreate,
    RenderJobRead,
)
from manim_renderer.service import LocalRenderService

from .settings import Settings, get_settings
from .storage import ObjectStore


class RenderClient:
    def __init__(self, settings: Settings | None = None, store: ObjectStore | None = None):
        self.settings = settings or get_settings()
        self.service = LocalRenderService(settings=self.settings, store=store)

    def submit_and_wait(
        self,
        request: RenderJobCreate,
        timeout_seconds: int,
        should_cancel: Callable[[], bool] | None = None,
    ) -> RenderJobRead:
        del timeout_seconds
        return self.service.render(request, should_cancel)

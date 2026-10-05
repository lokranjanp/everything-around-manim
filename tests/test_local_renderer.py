from pathlib import Path
from uuid import uuid4

from manim_agent.package import build_scene_package, package_hash
from manim_agent.settings import Settings
from manim_agent.storage import LocalObjectStore
from manim_contracts.models import RenderJobCreate, RenderProfile, RenderStatus
from manim_renderer.runner import FakeRunner
from manim_renderer.service import LocalRenderService

SOURCE = """from manim import *
class GeneratedScene(Scene):
    def construct(self):
        self.add(Dot())
"""


def make_service(tmp_path: Path) -> tuple[LocalRenderService, LocalObjectStore]:
    store = LocalObjectStore(tmp_path / "objects")
    settings = Settings(storage_root=tmp_path / "objects", render_runner="fake")
    return LocalRenderService(settings=settings, store=store, runner=FakeRunner()), store


def test_fake_local_render_completes(tmp_path: Path):
    service, store = make_service(tmp_path)
    raw, manifest = build_scene_package(
        generation_id=uuid4(),
        attempt=1,
        scene_class="GeneratedScene",
        source=SOURCE,
        profile=RenderProfile(quality="preview", width=640, height=360),
    )
    key = f"tests/{uuid4()}/scene.tar.gz"
    store.put_bytes(key, raw, "application/gzip")

    result = service.render(
        RenderJobCreate(package_key=key, package_sha256=package_hash(raw), manifest=manifest)
    )

    assert result.status == RenderStatus.COMPLETED
    assert {item.kind for item in result.artifacts} >= {"mp4", "png"}
    assert all(store.exists(item.object_key) for item in result.artifacts)


def test_local_render_reports_validation_failure(tmp_path: Path):
    service, store = make_service(tmp_path)
    raw, manifest = build_scene_package(
        generation_id=uuid4(),
        attempt=1,
        scene_class="GeneratedScene",
        source=SOURCE,
        profile=RenderProfile(quality="preview", width=640, height=360),
    )
    key = f"tests/{uuid4()}/scene.tar.gz"
    store.put_bytes(key, raw, "application/gzip")

    result = service.render(
        RenderJobCreate(package_key=key, package_sha256="0" * 64, manifest=manifest)
    )

    assert result.status == RenderStatus.FAILED
    assert result.error_code == "PACKAGE_REJECTED"


def test_local_render_can_be_cancelled_before_start(tmp_path: Path):
    service, store = make_service(tmp_path)
    raw, manifest = build_scene_package(
        generation_id=uuid4(),
        attempt=1,
        scene_class="GeneratedScene",
        source=SOURCE,
        profile=RenderProfile(quality="preview", width=640, height=360),
    )
    key = f"tests/{uuid4()}/scene.tar.gz"
    store.put_bytes(key, raw, "application/gzip")

    result = service.render(
        RenderJobCreate(package_key=key, package_sha256=package_hash(raw), manifest=manifest),
        should_cancel=lambda: True,
    )

    assert result.status == RenderStatus.CANCELLED

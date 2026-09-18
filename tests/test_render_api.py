from uuid import uuid4

from litestar.testing import TestClient

from manim_agent.package import build_scene_package, package_hash
from manim_contracts.models import RenderProfile, to_builtins
from manim_renderer import api
from manim_renderer.settings import get_render_settings
from manim_renderer.storage import get_render_store

SOURCE = """from manim import *
class GeneratedScene(Scene):
    def construct(self):
        self.add(Dot())
"""


def test_render_job_requires_internal_auth():
    with TestClient(api.app) as client:
        response = client.post("/internal/v1/render-jobs", json={})
    assert response.status_code in {401, 422}


def test_fake_render_job_completes():
    raw, manifest = build_scene_package(
        generation_id=uuid4(), attempt=1, scene_class="GeneratedScene", source=SOURCE,
        profile=RenderProfile(quality="preview", width=640, height=360),
    )
    key = f"tests/{uuid4()}/scene.tar.gz"
    get_render_store().put_bytes(key, raw, "application/gzip")
    headers = {"Authorization": f"Bearer {get_render_settings().internal_render_token}"}
    with TestClient(api.app) as client:
        response = client.post(
            "/internal/v1/render-jobs",
            headers=headers,
            json={"package_key": key, "package_sha256": package_hash(raw), "manifest": to_builtins(manifest)},
        )
        assert response.status_code == 202
        job_id = response.json()["id"]
        for _ in range(20):
            result = client.get(f"/internal/v1/render-jobs/{job_id}", headers=headers)
            if result.json()["status"] in {"completed", "failed"}:
                break
    assert result.json()["status"] == "completed"
    assert {item["kind"] for item in result.json()["artifacts"]} >= {"mp4", "png"}

from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from manim_agent.auth import hash_api_key
from manim_agent.db import Event, Generation, SessionLocal
from manim_agent.providers import FakeProvider
from manim_agent.settings import Settings
from manim_agent.storage import LocalObjectStore
from manim_agent.workflow import WorkflowEngine
from manim_contracts.models import Artifact, GenerationStatus, RenderJobRead, RenderStatus
from manim_renderer.runner import FakeRunner


class FakeRenderClient:
    def __init__(self, store: LocalObjectStore):
        self.store = store

    def submit_and_wait(
        self, request, timeout_seconds: int, should_cancel=None
    ) -> RenderJobRead:
        quality = request.manifest.profile.quality
        prefix = f"generations/{request.manifest.generation_id}/test/{quality}"
        artifacts = []
        for kind, name, data in (
            ("mp4", "final.mp4", b"video"),
            ("png", "final.png", FakeRunner.PNG),
            ("contact_sheet", "contact-sheet.png", FakeRunner.PNG),
        ):
            key = f"{prefix}/{name}"
            self.store.put_bytes(key, data, "application/octet-stream")
            artifacts.append(Artifact(kind=kind, object_key=key))
        return RenderJobRead(id=uuid4(), status=RenderStatus.COMPLETED, artifacts=artifacts)


def test_complete_agent_repair_pipeline(tmp_path: Path):
    store = LocalObjectStore(tmp_path / "objects")
    settings = Settings(storage_root=tmp_path / "objects", model_provider="fake", max_attempts=3)
    with SessionLocal() as session:
        generation = Generation(
            owner_key_hash=hash_api_key("dev-secret"), idempotency_key="workflow",
            prompt="Explain gradient descent visually", asset_ids=[],
            options={"duration_seconds": 30, "fps": 30}, max_attempts=3,
        )
        session.add(generation)
        session.commit()
        generation_id = generation.id

    WorkflowEngine(
        settings=settings,
        provider=FakeProvider(),
        store=store,
        render_client=FakeRenderClient(store),
    ).run(generation_id)

    with SessionLocal() as session:
        generation = session.get(Generation, generation_id)
        events = session.scalars(
            select(Event).where(Event.generation_id == generation_id).order_by(Event.id)
        ).all()
        assert generation.status == GenerationStatus.COMPLETED.value
        assert generation.attempt == 1
        assert generation.critic_score == 0.9
        assert {artifact["kind"] for artifact in generation.artifacts} >= {"mp4", "png"}
        assert GenerationStatus.CRITIQUING.value in {event.status for event in events}

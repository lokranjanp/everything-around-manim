from threading import Event

from manim_agent.auth import hash_api_key
from manim_agent.db import Generation, SessionLocal
from manim_contracts.models import GenerationStatus
from manim_control.queue import LocalGenerationQueue, recover_generations


def test_local_queue_deduplicates_pending_generation_ids():
    started = Event()
    release = Event()
    seen: list[str] = []

    def run(generation_id: str) -> None:
        seen.append(generation_id)
        started.set()
        release.wait(timeout=2)

    queue = LocalGenerationQueue(run)
    queue.start(recover=False)
    try:
        assert queue.submit("generation-1") is True
        assert started.wait(timeout=2)
        assert queue.submit("generation-1") is False
    finally:
        release.set()
        queue.stop()

    assert seen == ["generation-1"]


def test_startup_requeues_waiting_work_and_fails_interrupted_work():
    with SessionLocal() as session:
        queued = Generation(
            owner_key_hash=hash_api_key("dev-secret"),
            idempotency_key="queued",
            prompt="Queued generation",
            status=GenerationStatus.QUEUED.value,
        )
        active = Generation(
            owner_key_hash=hash_api_key("dev-secret"),
            idempotency_key="active",
            prompt="Interrupted generation",
            status=GenerationStatus.RENDERING_PREVIEW.value,
        )
        session.add_all([queued, active])
        session.commit()
        queued_id = queued.id
        active_id = active.id

    assert recover_generations() == [queued_id]

    with SessionLocal() as session:
        interrupted = session.get(Generation, active_id)
        assert interrupted.status == GenerationStatus.FAILED.value
        assert interrupted.error_code == "APP_RESTARTED"
        assert interrupted.events[0].event_key == "app-restarted"

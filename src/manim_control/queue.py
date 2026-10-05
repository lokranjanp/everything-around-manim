from __future__ import annotations

import logging
from collections.abc import Callable
from queue import Queue
from threading import Event as ThreadEvent
from threading import Lock, Thread

from sqlalchemy import select

from manim_agent.db import Event, Generation, SessionLocal
from manim_agent.workflow import run_generation
from manim_contracts.models import TERMINAL_STATUSES, GenerationStatus, utcnow

logger = logging.getLogger(__name__)


def recover_generations() -> list[str]:
    terminal = {status.value for status in TERMINAL_STATUSES}
    with SessionLocal() as session:
        generations = session.scalars(select(Generation).where(Generation.status.not_in(terminal))).all()
        queued: list[str] = []
        for generation in generations:
            if generation.status == GenerationStatus.QUEUED.value:
                queued.append(generation.id)
                continue
            generation.status = GenerationStatus.FAILED.value
            generation.error_code = "APP_RESTARTED"
            generation.error_message = "The local app stopped while this generation was running"
            generation.updated_at = utcnow()
            session.add(
                Event(
                    generation_id=generation.id,
                    status=GenerationStatus.FAILED.value,
                    message="Generation interrupted by local app restart",
                    event_key="app-restarted",
                )
            )
        session.commit()
        return queued


class LocalGenerationQueue:
    def __init__(self, runner: Callable[[str], None] = run_generation):
        self._runner = runner
        self._queue: Queue[str | None] = Queue()
        self._pending: set[str] = set()
        self._lock = Lock()
        self._stopping = ThreadEvent()
        self._thread: Thread | None = None

    def start(self, *, recover: bool = True) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stopping.clear()
            self._thread = Thread(target=self._work, name="manim-local-worker", daemon=True)
            self._thread.start()
        if recover:
            for generation_id in recover_generations():
                self.submit(generation_id)

    def submit(self, generation_id: str) -> bool:
        with self._lock:
            if self._stopping.is_set() or generation_id in self._pending:
                return False
            self._pending.add(generation_id)
        self._queue.put(generation_id)
        return True

    def stop(self) -> None:
        self._stopping.set()
        self._queue.put(None)
        thread = self._thread
        if thread:
            thread.join(timeout=2)

    def _work(self) -> None:
        while not self._stopping.is_set():
            generation_id = self._queue.get()
            if generation_id is None:
                self._queue.task_done()
                return
            try:
                self._runner(generation_id)
            except Exception:
                logger.exception("generation worker crashed", extra={"generation_id": generation_id})
            finally:
                with self._lock:
                    self._pending.discard(generation_id)
                self._queue.task_done()


generation_queue = LocalGenerationQueue()


def start_generation_queue() -> None:
    generation_queue.start()


def stop_generation_queue() -> None:
    generation_queue.stop()


def dispatch_generation(generation_id: str) -> None:
    generation_queue.submit(generation_id)

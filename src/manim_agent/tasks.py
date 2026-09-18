from celery import Celery

from .settings import get_settings
from .workflow import run_generation

settings = get_settings()
celery_app = Celery("manim-agent", broker=settings.broker_url)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_serializer="json",
    accept_content=["json"],
)


@celery_app.task(name="manim_agent.run_generation", autoretry_for=(), acks_late=True)
def run_generation_task(generation_id: str) -> None:
    run_generation(generation_id)


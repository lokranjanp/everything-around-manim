from celery import Celery

from .service import execute_render_job
from .settings import get_render_settings

settings = get_render_settings()
celery_app = Celery("manim-renderer", broker=settings.broker_url)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_serializer="json",
    accept_content=["json"],
    task_routes={"manim_renderer.execute": {"queue": "renders"}},
)


@celery_app.task(name="manim_renderer.execute", acks_late=True)
def execute_render_task(job_id: str) -> None:
    execute_render_job(job_id)


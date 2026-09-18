from celery import Celery

from manim_agent.settings import get_settings


def dispatch_generation(generation_id: str) -> None:
    settings = get_settings()
    Celery("manim-control-publisher", broker=settings.broker_url).send_task(
        "manim_agent.run_generation", args=[generation_id]
    )

from __future__ import annotations

from datetime import timedelta

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import select

from manim_contracts.models import TERMINAL_STATUSES, utcnow

from .settings import get_settings


def setup_checkpoints() -> None:
    url = get_settings().langgraph_database_url
    if not url:
        raise RuntimeError("LANGGRAPH_DATABASE_URL is required")
    with PostgresSaver.from_conn_string(url) as saver:
        saver.setup()


def cleanup_expired_checkpoints() -> int:
    from .db import Generation, SessionLocal

    settings = get_settings()
    if not settings.langgraph_database_url:
        return 0
    cutoff = utcnow() - timedelta(days=settings.checkpoint_retention_days)
    terminal = [status.value for status in TERMINAL_STATUSES]
    with SessionLocal() as session:
        thread_ids = list(session.scalars(select(Generation.id).where(
            Generation.status.in_(terminal), Generation.updated_at < cutoff
        )))
    if not thread_ids:
        return 0
    with psycopg.connect(settings.langgraph_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM checkpoint_writes WHERE thread_id = ANY(%s)", (thread_ids,))
            cursor.execute("DELETE FROM checkpoint_blobs WHERE thread_id = ANY(%s)", (thread_ids,))
            cursor.execute("DELETE FROM checkpoints WHERE thread_id = ANY(%s)", (thread_ids,))
        connection.commit()
    return len(thread_ids)


if __name__ == "__main__":
    setup_checkpoints()

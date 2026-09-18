FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LANGGRAPH_STRICT_MSGPACK=true
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir '.[agent]'
RUN python -c "import langgraph, pydantic"
RUN useradd --create-home --uid 10001 agent
USER 10001:10001
CMD ["celery", "-A", "manim_agent.tasks:celery_app", "worker", "--loglevel=INFO", "--concurrency=2"]

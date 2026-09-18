FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
RUN useradd --create-home --uid 10001 app
USER 10001:10001
CMD ["uvicorn", "manim_agent.api:app", "--host", "0.0.0.0", "--port", "8000"]


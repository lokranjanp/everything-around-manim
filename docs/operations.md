# Operations

Build the runtime image once, then start the API:

```bash
docker build -f docker/runtime.Dockerfile -t agentic-manim-runtime:local .
uvicorn manim_control.api:app --host 127.0.0.1 --port 8000
```

The app creates its SQLite schema and local object directory automatically. Health endpoints are
`/health/live` and `/health/ready`. Standard process logs are the only operational telemetry.

Back up `DATA_ROOT` to preserve both application state and artifacts. A clean shutdown leaves
queued generations recoverable. If the process exits during active work, that generation is marked
failed at the next startup; submit a new generation to retry it.

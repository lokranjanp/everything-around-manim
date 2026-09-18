# Operations

Run agent and renderer migrations independently:

```bash
alembic -c alembic-agent.ini upgrade head
alembic -c alembic-renderer.ini upgrade head
```

Health endpoints are `/health/live` and `/health/ready`; Prometheus metrics are at `/metrics`.
Set `OTEL_EXPORTER_OTLP_ENDPOINT` to export HTTP traces. Correlate services with the generation and
render job IDs present in API records and structured task logs.

Scale agent workers by the default Celery queue and render workers by the `renders` queue. A render
worker has concurrency one because each task owns a separately bounded runtime job. In Kubernetes,
use KEDA or the platform queue adapter to scale from RabbitMQ queue depth.

Back up both PostgreSQL databases and the object bucket. Jobs left non-terminal after a worker loss
are safe to redeliver because generation creation, package submission, and artifact keys are
idempotent. Object lifecycle policies may remove abandoned preview attempts after the desired
retention period while retaining final artifacts and manifests.

# Architecture

## Trust boundaries

The public control plane owns prompts, user-visible state, model access, and the repair loop. It
cannot execute Manim because neither its image nor dependency set contains Manim, ffmpeg, LaTeX,
Docker, or Kubernetes clients.

The render control plane accepts only a `SceneManifest` v1 package reference through its internal
authenticated API. It verifies the package archive, checks the embedded manifest and hashes,
parses the source AST, downloads and verifies declared assets, and launches a runtime workload.
Model credentials are never supplied to this workload.

The runtime workload is disposable. It receives read-only source/assets and writable output space,
has no network, runs as UID 1000 with all capabilities dropped, and is bounded by process, CPU,
memory, output, and wall-clock limits.

## Durable flow

1. The API commits a generation and initial event before publishing work.
2. The agent worker persists the storyboard/design, creates an immutable source package, and asks
   the render service for a preview.
3. The critic reviews sampled frames plus intent and sanitized logs. Failed critique produces a
   targeted next attempt; three total attempts are allowed.
4. Passing source is rendered once at final quality. Exhausted repair budgets return the best
   preview with `completed_with_warnings`.
5. Revisions create child generations; parents and artifacts remain immutable.

PostgreSQL is authoritative for job state. RabbitMQ provides at-least-once work delivery, so both
agent and render submissions are idempotent. S3-compatible object storage holds assets, source
packages, previews, diagnostics, and final artifacts.

## Deployment

Compose uses a render dispatcher with Docker socket access; that service must remain private.
Production Kubernetes uses the `kubernetes` runner, a dedicated service account, one short-lived
Job per render, an RWX staging PVC, and a deny-all runtime NetworkPolicy. PostgreSQL, RabbitMQ, and
S3 are expected to be managed services in production.


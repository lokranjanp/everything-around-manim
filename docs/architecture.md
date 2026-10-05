# Architecture

Agentic Manim is a single local Litestar application. SQLite is authoritative for generation,
asset, revision, and event state. Files under `DATA_ROOT/objects` hold uploads, source packages,
previews, diagnostics, and final artifacts.

A daemon worker consumes generation IDs from an in-process queue and executes one LangGraph
workflow at a time. Queued work is recovered when the app starts. Work interrupted after it began
is marked failed because graph checkpoints intentionally live only for the lifetime of a run.

The workflow validates every versioned scene package, verifies declared assets and hashes, and
launches Docker directly for preview and final rendering. The runtime container receives only
read-only source/assets and writable output space. It has no network or model credentials, runs as
UID 1000 with all capabilities dropped, and has process, CPU, memory, and wall-clock limits.

Failed visual critique produces a targeted repair attempt, up to three attempts. Passing source is
rendered once at final quality; an exhausted repair budget returns the best preview with
`completed_with_warnings`. Revisions remain immutable child generations.

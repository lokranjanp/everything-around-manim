# Agentic Manim

Local-first API that turns a prompt into a reviewed Manim animation. Application state lives in
SQLite, artifacts live on the local filesystem, and generated Python runs inside an isolated
Docker container.

## Architecture

```text
client -> Litestar API -> local background worker -> LangGraph
                                                     |
                                                     v
                                      isolated Manim container
                                                     |
                                             visual critic loop
```

The API stores generation state in `.data/agent.db`, saves uploads and render artifacts under
`.data/objects`, and emits progress through Server-Sent Events. A single local worker executes the
LangGraph workflow. Scene packages are validated before Docker runs them without network access or
model credentials.

## Local development

1. Create a Python 3.11 or 3.12 virtual environment and run `pip install -e .`.
2. Copy `.env.example` to `.env`. Keep `MODEL_PROVIDER=fake` for a credential-free workflow, or
   set `OPENAI_API_KEY` and choose OpenAI model IDs.
3. Build the isolated rendering image:

```bash
docker build -f docker/runtime.Dockerfile -t agentic-manim-runtime:local .
```

4. Start the single local API:

```bash
uvicorn manim_control.api:app --host 127.0.0.1 --port 8000
```

5. Create a generation:

```bash
curl -X POST http://localhost:8000/v1/generations \
  -H 'X-API-Key: dev-secret' \
  -H 'Idempotency-Key: gradient-demo' \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Explain gradient descent visually"}'
```

The API is documented at `http://localhost:8000/docs`. Set `DATA_ROOT` to move the SQLite database
and artifact directory somewhere other than `.data`.

## Security boundary

The local app launches a short-lived rendering container with no network, a read-only root
filesystem, dropped capabilities, a non-root identity, process/memory/CPU limits, and ephemeral
temporary filesystems. Model credentials are never copied into the container. The fake renderer is
for tests and API development only.

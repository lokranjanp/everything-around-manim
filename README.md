# Agentic Manim

API-first platform that turns a prompt into a reviewed Manim animation while keeping generated
Python and the Manim toolchain outside the agent control plane.

## Architecture

```text
client -> Litestar control API -> Celery -> LangGraph agent worker
                                              |
                                              v
                                  render API -> isolated Manim container
                                      ^                 |
                                      +-- visual critic-+
```

The control plane stores the public generation projection and emits Server-Sent Events. The agent
worker executes a checkpointed LangGraph and uses LangChain chat-model interfaces for structured
text and vision calls. The render service validates a versioned scene package, then runs it without
model credentials or network access.

Public and render-service contracts use `msgspec`. Pydantic is absent from those images and exists
only as a transitive LangGraph/LangChain dependency in the separate agent image.

## Local development

1. Copy `.env.example` to `.env`.
2. Set `MODEL_PROVIDER=fake` for a credential-free workflow, or set `OPENAI_API_KEY` and choose
   OpenAI model IDs.
3. Run `docker compose up --build`. Compose initializes the LangGraph checkpoint schema first.
4. Create a generation:

```bash
curl -X POST http://localhost:8000/v1/generations \
  -H 'X-API-Key: dev-secret' \
  -H 'Idempotency-Key: gradient-demo' \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Explain gradient descent visually"}'
```

The public API is documented at `http://localhost:8000/docs`; render-service documentation is at
`http://localhost:8010/docs` and requires the internal service token for job endpoints.

## Security boundary

Agent containers do not install Manim, LaTeX, ffmpeg, or the Docker SDK. The render dispatcher is a
separate deployable service. In local Compose it launches a short-lived child container with no
network, read-only source, dropped capabilities, non-root identity, process/memory/CPU limits, and
an ephemeral filesystem. Kubernetes deployments use one locked-down Job per render.

Do not expose the render API or Docker socket proxy publicly. The fake renderer is for tests and
API development only.

# Renderer security model

Generated Python is untrusted even when prompts come from a trusted team. Static validation is a
fast rejection layer, not the security boundary. The runtime container is the boundary.

Controls implemented:

- fixed, hash-checked archive members and a versioned manifest;
- explicit import and dangerous-call denial through Python AST inspection;
- asset ownership checks in the public API and independent size/hash/type checks in the renderer;
- no runtime network or model credentials;
- non-root identity, read-only root filesystem, dropped Linux capabilities, no-new-privileges,
  process/memory/CPU/time limits, and ephemeral filesystems;
- fixed runtime image and entrypoint controlled by the local app;
- LaTeX and ffmpeg are reachable only through the trusted runtime entrypoint;

Keep the API bound to localhost unless an authenticated reverse proxy protects it. Docker remains
the security boundary for generated Python, so do not switch `RENDER_RUNNER` away from `docker`
outside tests. Protect `.env` and `DATA_ROOT`, and keep Docker Desktop or the Docker engine updated.

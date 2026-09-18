# Renderer security model

Generated Python is untrusted even when prompts come from a trusted team. Static validation is a
fast rejection layer, not the security boundary. The runtime container or Pod is the boundary.

Controls implemented:

- fixed, hash-checked archive members and a versioned manifest;
- explicit import and dangerous-call denial through Python AST inspection;
- asset ownership checks in the public API and independent size/hash/type checks in the renderer;
- no runtime network or service-account token;
- non-root identity, read-only root filesystem, dropped Linux capabilities, no-new-privileges,
  process/memory/CPU/time limits, and ephemeral filesystems;
- fixed runtime image and entrypoint controlled by the render service;
- LaTeX and ffmpeg are reachable only through the trusted runtime entrypoint;
- internal render API token separated from public API keys and model credentials.

Production should additionally apply image signature verification, admission policy, seccomp and
AppArmor profiles appropriate to the cluster, object lifecycle policies, secret rotation, and
central alerting for package rejection and runtime failure rates.


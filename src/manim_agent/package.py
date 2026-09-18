from __future__ import annotations

import hashlib
import io
import tarfile
from uuid import UUID

from manim_contracts.models import AssetRef, RenderProfile, SceneManifest, encode_json


def build_scene_package(
    *,
    generation_id: UUID,
    attempt: int,
    scene_class: str,
    source: str,
    profile: RenderProfile,
    assets: list[AssetRef] | None = None,
) -> tuple[bytes, SceneManifest]:
    source_bytes = source.encode("utf-8")
    manifest = SceneManifest(
        generation_id=generation_id,
        attempt=attempt,
        scene_class=scene_class,
        source_sha256=hashlib.sha256(source_bytes).hexdigest(),
        profile=profile,
        assets=assets or [],
    )
    manifest_bytes = encode_json(manifest, indent=2)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in (("scene.py", source_bytes), ("manifest.json", manifest_bytes)):
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mode = 0o444
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue(), manifest


def package_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

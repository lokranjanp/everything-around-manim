from uuid import uuid4

import pytest
from pydantic import ValidationError

from manim_contracts.models import (
    CriticReport,
    GeneratedScene,
    RenderProfile,
    SceneDesign,
    SceneManifest,
    Storyboard,
)


def test_render_profile_is_bounded_and_sixteen_by_nine():
    profile = RenderProfile(width=1920, height=1080, fps=30, max_duration_seconds=180)
    assert profile.width == 1920
    with pytest.raises(ValidationError):
        RenderProfile(width=1000, height=1000)
    with pytest.raises(ValidationError):
        RenderProfile(width=1920, height=1080, fps=60)


def test_manifest_rejects_unversioned_or_unhashed_input():
    with pytest.raises(ValidationError):
        SceneManifest(
            schema_version="2.0",
            generation_id=uuid4(),
            attempt=1,
            scene_class="Scene1",
            source_sha256="bad",
            profile=RenderProfile(),
        )


@pytest.mark.parametrize("schema", [Storyboard, SceneDesign, GeneratedScene, CriticReport])
def test_agent_schemas_are_compatible_with_strict_structured_outputs(schema):
    document = schema.model_json_schema()
    objects = [document, *document.get("$defs", {}).values()]
    for node in objects:
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False
            assert set(node.get("required", [])) == set(node.get("properties", {}))

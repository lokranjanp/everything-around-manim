from uuid import uuid4

import pytest

from manim_agent.package import build_scene_package, package_hash
from manim_contracts.models import RenderProfile
from manim_renderer.validator import PackageValidationError, validate_package

SAFE = """from manim import *

class SafeScene(Scene):
    def construct(self):
        self.add(Text("safe"))
"""


def make_package(source: str = SAFE):
    raw, manifest = build_scene_package(
        generation_id=uuid4(), attempt=1, scene_class="SafeScene",
        source=source, profile=RenderProfile(quality="preview", width=640, height=360),
    )
    return raw, manifest


def test_valid_scene_package_round_trips():
    raw, manifest = make_package()
    validated = validate_package(raw, manifest, package_hash(raw), 10 * 1024 * 1024)
    assert validated.source.decode() == SAFE


@pytest.mark.parametrize(
    "source",
    [
        "import os\nfrom manim import *\nclass SafeScene(Scene): pass",
        "from manim import *\nclass SafeScene(Scene):\n def construct(self): open('/etc/passwd')",
        "import socket\nfrom manim import *\nclass SafeScene(Scene): pass",
        "from subprocess import run\nfrom manim import *\nclass SafeScene(Scene): pass",
    ],
)
def test_dangerous_source_is_rejected(source: str):
    raw, manifest = make_package(source)
    with pytest.raises(PackageValidationError):
        validate_package(raw, manifest, package_hash(raw), 10 * 1024 * 1024)


def test_package_checksum_is_mandatory():
    raw, manifest = make_package()
    with pytest.raises(PackageValidationError, match="checksum"):
        validate_package(raw, manifest, "0" * 64, 10 * 1024 * 1024)


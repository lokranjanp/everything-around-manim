from __future__ import annotations

import ast
import hashlib
import io
import tarfile
from dataclasses import dataclass

from manim_contracts.models import SceneManifest, decode_json


class PackageValidationError(ValueError):
    pass


ALLOWED_IMPORT_ROOTS = {"manim", "math", "numpy"}
DENIED_CALLS = {"breakpoint", "compile", "eval", "exec", "globals", "input", "locals", "open", "__import__"}
DENIED_ATTRIBUTES = {
    "environ", "fork", "kill", "popen", "remove", "rename", "replace", "rmdir",
    "spawn", "system", "unlink", "walk",
}


@dataclass(frozen=True)
class ValidatedPackage:
    source: bytes
    manifest: SceneManifest


class ScenePolicyVisitor(ast.NodeVisitor):
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name.split(".")[0] not in ALLOWED_IMPORT_ROOTS:
                raise PackageValidationError(f"import not allowed: {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level or not node.module or node.module.split(".")[0] not in ALLOWED_IMPORT_ROOTS:
            raise PackageValidationError(f"import not allowed: {node.module}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in DENIED_CALLS:
            raise PackageValidationError(f"call not allowed: {node.func.id}")
        if isinstance(node.func, ast.Attribute) and node.func.attr in DENIED_ATTRIBUTES:
            raise PackageValidationError(f"attribute call not allowed: {node.func.attr}")
        self.generic_visit(node)


def validate_package(raw: bytes, expected: SceneManifest, expected_hash: str, max_bytes: int) -> ValidatedPackage:
    if len(raw) > max_bytes:
        raise PackageValidationError("package exceeds maximum size")
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise PackageValidationError("package checksum mismatch")
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
            members = archive.getmembers()
            names = {member.name for member in members}
            if names != {"scene.py", "manifest.json"}:
                raise PackageValidationError("package must contain only scene.py and manifest.json")
            if any(member.issym() or member.islnk() or not member.isfile() for member in members):
                raise PackageValidationError("links and non-files are not allowed")
            if any(member.size > max_bytes for member in members):
                raise PackageValidationError("package member exceeds maximum size")
            source = archive.extractfile("scene.py").read()
            manifest_raw = archive.extractfile("manifest.json").read()
    except (tarfile.TarError, KeyError, AttributeError) as exc:
        raise PackageValidationError("invalid package archive") from exc

    embedded = decode_json(manifest_raw, SceneManifest)
    if embedded != expected:
        raise PackageValidationError("embedded manifest does not match request")
    if hashlib.sha256(source).hexdigest() != embedded.source_sha256:
        raise PackageValidationError("source checksum mismatch")
    try:
        tree = ast.parse(source.decode("utf-8"), filename="scene.py")
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise PackageValidationError(f"invalid Python source: {exc}") from exc
    ScenePolicyVisitor().visit(tree)
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    scene = classes.get(embedded.scene_class)
    if scene is None:
        raise PackageValidationError("manifest scene class is not defined")
    allowed_bases = {"Scene", "ThreeDScene", "MovingCameraScene", "ZoomedScene"}
    base_names = {base.id for base in scene.bases if isinstance(base, ast.Name)}
    if not base_names.intersection(allowed_bases):
        raise PackageValidationError("scene class must inherit from an allowed Manim Scene")
    return ValidatedPackage(source=source, manifest=embedded)

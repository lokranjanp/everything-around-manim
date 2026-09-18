from __future__ import annotations

import json
import traceback
from uuid import UUID

from sqlalchemy import select

from manim_contracts.models import (
    TERMINAL_STATUSES,
    AssetRef,
    CriticReport,
    GeneratedScene,
    GenerationStatus,
    RenderJobCreate,
    RenderProfile,
    RenderStatus,
    SceneDesign,
    Storyboard,
)

from .db import Asset, Generation, SessionLocal, add_event
from .package import build_scene_package, package_hash
from .prompts import (
    CODER_INSTRUCTIONS,
    CRITIC_INSTRUCTIONS,
    DESIGNER_INSTRUCTIONS,
    PLANNER_INSTRUCTIONS,
)
from .providers import ModelProvider, get_model_provider
from .render_client import RenderClient
from .settings import Settings, get_settings
from .storage import ObjectStore, get_object_store


class WorkflowEngine:
    def __init__(
        self,
        settings: Settings | None = None,
        provider: ModelProvider | None = None,
        store: ObjectStore | None = None,
        render_client: RenderClient | None = None,
    ):
        self.settings = settings or get_settings()
        self.provider = provider or get_model_provider(self.settings)
        self.store = store or get_object_store(self.settings)
        self.render_client = render_client or RenderClient(self.settings)

    def _load(self, session, generation_id: str) -> Generation:
        generation = session.scalar(select(Generation).where(Generation.id == generation_id))
        if generation is None:
            raise LookupError(f"generation {generation_id} not found")
        return generation

    def _cancelled(self, session, generation_id: str) -> bool:
        session.expire_all()
        return self._load(session, generation_id).status == GenerationStatus.CANCELLED.value

    @staticmethod
    def _cancel_requested(generation_id: str) -> bool:
        with SessionLocal() as session:
            generation = session.get(Generation, generation_id)
            return generation is not None and generation.status == GenerationStatus.CANCELLED.value

    def run(self, generation_id: str) -> None:
        try:
            self._run(generation_id)
        except Exception as exc:
            with SessionLocal() as session:
                generation = self._load(session, generation_id)
                if generation.status != GenerationStatus.CANCELLED.value:
                    generation.error_code = type(exc).__name__
                    generation.error_message = str(exc)[:4000]
                    data = dict(generation.workflow_data or {})
                    data["traceback"] = traceback.format_exc(limit=20)
                    generation.workflow_data = data
                    add_event(session, generation, GenerationStatus.FAILED, "Workflow failed")

    def _run(self, generation_id: str) -> None:
        with SessionLocal() as session:
            generation = self._load(session, generation_id)
            if GenerationStatus(generation.status) in TERMINAL_STATUSES:
                return
            add_event(session, generation, GenerationStatus.PLANNING, "Planning storyboard")
            storyboard = self.provider.generate_structured(
                stage="planner",
                instructions=PLANNER_INSTRUCTIONS,
                prompt=f"Prompt: {generation.prompt}\nOptions: {json.dumps(generation.options)}",
                schema=Storyboard,
            )
            if self._cancelled(session, generation_id):
                return
            add_event(session, generation, GenerationStatus.DESIGNING, "Designing scene choreography")
            design = self.provider.generate_structured(
                stage="designer",
                instructions=DESIGNER_INSTRUCTIONS,
                prompt=storyboard.model_dump_json(),
                schema=SceneDesign,
            )
            generation.workflow_data = {
                "storyboard": storyboard.model_dump(mode="json"),
                "design": design.model_dump(mode="json"),
            }
            assets = session.scalars(
                select(Asset).where(Asset.id.in_(generation.asset_ids))
            ).all() if generation.asset_ids else []
            asset_refs = [
                AssetRef(
                    asset_id=UUID(asset.id),
                    object_key=asset.object_key,
                    filename=asset.filename,
                    sha256=asset.sha256,
                    media_type=asset.media_type,
                    size_bytes=asset.size_bytes,
                )
                for asset in assets
            ]
            session.commit()

        best: tuple[float, list[dict], CriticReport, str, str] | None = None
        repair_context = ""
        for attempt in range(1, self.settings.max_attempts + 1):
            with SessionLocal() as session:
                generation = self._load(session, generation_id)
                if self._cancelled(session, generation_id):
                    return
                generation.attempt = attempt
                add_event(session, generation, GenerationStatus.GENERATING, f"Generating scene attempt {attempt}")
                scene = self.provider.generate_structured(
                    stage="code_generator",
                    instructions=CODER_INSTRUCTIONS,
                    prompt=(
                        f"User prompt: {generation.prompt}\nStoryboard: {storyboard.model_dump_json()}\n"
                        f"Design: {design.model_dump_json()}\n"
                        f"Available assets: {[a.runtime_path for a in asset_refs]}\n"
                        f"Prior repair request: {repair_context}"
                    ),
                    schema=GeneratedScene,
                )
                preview_profile = RenderProfile(
                    quality="preview", width=640, height=360, fps=15,
                    max_duration_seconds=generation.options.get("duration_seconds", 60),
                )
                package, manifest = build_scene_package(
                    generation_id=UUID(generation.id), attempt=attempt,
                    scene_class=scene.scene_class, source=scene.source, profile=preview_profile,
                    assets=asset_refs,
                )
                package_key = f"generations/{generation.id}/attempts/{attempt}/scene.tar.gz"
                self.store.put_bytes(package_key, package, "application/gzip")
                add_event(session, generation, GenerationStatus.VALIDATING, "Submitting validated package")
                add_event(session, generation, GenerationStatus.RENDERING_PREVIEW, "Rendering preview")

            render = self.render_client.submit_and_wait(
                RenderJobCreate(package_key=package_key, package_sha256=package_hash(package), manifest=manifest),
                timeout_seconds=300,
                should_cancel=lambda: self._cancel_requested(generation_id),
            )
            if render.status != RenderStatus.COMPLETED:
                repair_context = f"Render failed: {render.error_code}: {render.error_message}\n{render.logs[-3000:]}"
                with SessionLocal() as session:
                    generation = self._load(session, generation_id)
                    data = dict(generation.workflow_data or {})
                    attempts = list(data.get("attempts", []))
                    attempts.append(
                        {
                            "attempt": attempt,
                            "render_status": render.status.value,
                            "error_code": render.error_code,
                            "error_message": render.error_message,
                        }
                    )
                    data["attempts"] = attempts
                    generation.workflow_data = data
                    session.commit()
                continue

            with SessionLocal() as session:
                generation = self._load(session, generation_id)
                add_event(session, generation, GenerationStatus.CRITIQUING, "Critiquing sampled frames")
                image_bytes = [
                    self.store.get_bytes(a.object_key)
                    for a in render.artifacts
                    if a.kind in {"png", "contact_sheet"} and self.store.exists(a.object_key)
                ]
                report = self.provider.analyze_images(
                    instructions=CRITIC_INSTRUCTIONS,
                    prompt=f"Original request: {generation.prompt}\nRender logs: {render.logs[-2000:]}",
                    images=image_bytes,
                    schema=CriticReport,
                )
                score = report.aggregate_score
                generation.critic_score = score
                attempt_artifacts = [a.model_dump(mode="json") for a in render.artifacts]
                data = dict(generation.workflow_data or {})
                attempts = list(data.get("attempts", []))
                attempts.append(
                    {
                        "attempt": attempt,
                        "render_status": render.status.value,
                        "critic": report.model_dump(mode="json"),
                        "score": score,
                    }
                )
                data["attempts"] = attempts
                generation.workflow_data = data
                if best is None or score > best[0]:
                    best = (
                        score,
                        attempt_artifacts,
                        report,
                        package_key,
                        package_hash(package),
                    )
                session.commit()
                if report.approved and not report.blocking_issues and score >= self.settings.critic_threshold:
                    self._render_final(session, generation, scene, attempt, asset_refs)
                    return
                repair_context = json.dumps(
                    {"issues": report.blocking_issues, "instructions": report.repair_instructions}
                )
                if attempt < self.settings.max_attempts:
                    add_event(session, generation, GenerationStatus.REPAIRING, "Preparing targeted repair")

        with SessionLocal() as session:
            generation = self._load(session, generation_id)
            if best is None:
                raise RuntimeError("all render attempts failed")
            generation.artifacts = best[1]
            generation.critic_score = best[0]
            generation.warning = "Automatic repair budget exhausted; returning the best preview"
            self._attach_diagnostics(
                generation,
                source_key=best[3],
                source_sha256=best[4],
            )
            add_event(
                session, generation, GenerationStatus.COMPLETED_WITH_WARNINGS,
                "Repair budget exhausted; best result selected",
            )

    def _render_final(
        self,
        session,
        generation: Generation,
        scene: GeneratedScene,
        attempt: int,
        assets: list[AssetRef],
    ) -> None:
        profile = RenderProfile(
            quality="final", width=1920, height=1080,
            fps=generation.options.get("fps", 30),
            max_duration_seconds=generation.options.get("duration_seconds", 60),
        )
        package, manifest = build_scene_package(
            generation_id=UUID(generation.id), attempt=attempt,
            scene_class=scene.scene_class, source=scene.source, profile=profile,
            assets=assets,
        )
        key = f"generations/{generation.id}/final/scene.tar.gz"
        self.store.put_bytes(key, package, "application/gzip")
        add_event(session, generation, GenerationStatus.RENDERING_FINAL, "Rendering final 1080p output")
        final = self.render_client.submit_and_wait(
            RenderJobCreate(package_key=key, package_sha256=package_hash(package), manifest=manifest),
            timeout_seconds=900,
            should_cancel=lambda: self._cancel_requested(generation.id),
        )
        if final.status != RenderStatus.COMPLETED:
            raise RuntimeError(f"final render failed: {final.error_message}")
        generation.artifacts = [a.model_dump(mode="json") for a in final.artifacts]
        self._attach_diagnostics(
            generation,
            source_key=key,
            source_sha256=package_hash(package),
        )
        add_event(session, generation, GenerationStatus.COMPLETED, "Generation completed")

    def _attach_diagnostics(
        self,
        generation: Generation,
        *,
        source_key: str,
        source_sha256: str,
    ) -> None:
        diagnostics = json.dumps(
            {
                "generation_id": generation.id,
                "attempt": generation.attempt,
                "critic_score": generation.critic_score,
                "warning": generation.warning,
                "workflow": generation.workflow_data,
            },
            indent=2,
            default=str,
        ).encode()
        diagnostics_key = f"generations/{generation.id}/diagnostics.json"
        self.store.put_bytes(diagnostics_key, diagnostics, "application/json")
        artifacts = list(generation.artifacts or [])
        artifacts.extend(
            [
                {
                    "kind": "source",
                    "object_key": source_key,
                    "sha256": source_sha256,
                    "size_bytes": None,
                    "download_url": None,
                },
                {
                    "kind": "diagnostics",
                    "object_key": diagnostics_key,
                    "sha256": package_hash(diagnostics),
                    "size_bytes": len(diagnostics),
                    "download_url": None,
                },
            ]
        )
        generation.artifacts = artifacts


def run_generation(generation_id: str) -> None:
    WorkflowEngine().run(generation_id)

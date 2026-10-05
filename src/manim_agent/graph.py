from __future__ import annotations

import json
import traceback
from contextlib import nullcontext
from typing import Any, Literal, TypedDict
from uuid import UUID

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy
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
    convert,
    encode_json,
    to_builtins,
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


class AnimationState(TypedDict, total=False):
    generation_id: str
    prompt: str
    options: dict[str, Any]
    assets: list[dict[str, Any]]
    storyboard: dict[str, Any]
    design: dict[str, Any]
    scene: dict[str, Any]
    attempt: int
    max_attempts: int
    repair_context: str
    render: dict[str, Any]
    render_failed: bool
    critic: dict[str, Any]
    score: float
    accepted: bool
    best: dict[str, Any]
    package_key: str
    package_sha256: str
    cancelled: bool


class AnimationGraph:
    def __init__(self, settings: Settings | None = None, provider: ModelProvider | None = None,
                 store: ObjectStore | None = None, render_client: RenderClient | None = None,
                 checkpointer: Any | None = None):
        self.settings = settings or get_settings()
        self.provider = provider or get_model_provider(self.settings)
        self.store = store or get_object_store(self.settings)
        self.render_client = render_client or RenderClient(self.settings)
        self.checkpointer = checkpointer

    @staticmethod
    def _load(session, generation_id: str) -> Generation:
        generation = session.scalar(select(Generation).where(Generation.id == generation_id))
        if generation is None:
            raise LookupError(f"generation {generation_id} not found")
        return generation

    @staticmethod
    def _cancel_requested(generation_id: str) -> bool:
        with SessionLocal() as session:
            generation = session.get(Generation, generation_id)
            return generation is not None and generation.status == GenerationStatus.CANCELLED.value

    def hydrate(self, state: AnimationState) -> AnimationState:
        with SessionLocal() as session:
            generation = self._load(session, state["generation_id"])
            if GenerationStatus(generation.status) in TERMINAL_STATUSES:
                return {"cancelled": True}
            assets = (session.scalars(select(Asset).where(Asset.id.in_(generation.asset_ids))).all()
                      if generation.asset_ids else [])
            refs = [AssetRef(asset_id=UUID(a.id), object_key=a.object_key, filename=a.filename,
                             sha256=a.sha256, media_type=a.media_type, size_bytes=a.size_bytes)
                    for a in assets]
            return {"prompt": generation.prompt, "options": generation.options,
                    "assets": [to_builtins(ref) for ref in refs], "attempt": generation.attempt,
                    "max_attempts": generation.max_attempts, "repair_context": ""}

    def plan(self, state: AnimationState) -> AnimationState:
        with SessionLocal() as session:
            add_event(session, self._load(session, state["generation_id"]),
                      GenerationStatus.PLANNING, "Planning storyboard", "plan")
        storyboard = self.provider.generate_structured(
            stage="planner", instructions=PLANNER_INSTRUCTIONS,
            prompt=f"Prompt: {state['prompt']}\nOptions: {json.dumps(state['options'])}", schema=Storyboard)
        return {"storyboard": to_builtins(storyboard),
                "cancelled": self._cancel_requested(state["generation_id"])}

    def design(self, state: AnimationState) -> AnimationState:
        with SessionLocal() as session:
            add_event(session, self._load(session, state["generation_id"]),
                      GenerationStatus.DESIGNING, "Designing scene choreography", "design")
        design = self.provider.generate_structured(stage="designer", instructions=DESIGNER_INSTRUCTIONS,
                                                   prompt=encode_json(state["storyboard"]).decode(),
                                                   schema=SceneDesign)
        with SessionLocal() as session:
            generation = self._load(session, state["generation_id"])
            generation.workflow_data = {"storyboard": state["storyboard"], "design": to_builtins(design)}
            session.commit()
        return {"design": to_builtins(design),
                "cancelled": self._cancel_requested(state["generation_id"])}

    def generate_scene(self, state: AnimationState) -> AnimationState:
        attempt = state.get("attempt", 0) + 1
        with SessionLocal() as session:
            generation = self._load(session, state["generation_id"])
            generation.attempt = attempt
            add_event(session, generation, GenerationStatus.GENERATING,
                      f"Generating scene attempt {attempt}", f"generate:{attempt}")
        scene = self.provider.generate_structured(
            stage="code_generator", instructions=CODER_INSTRUCTIONS,
            prompt=(f"User prompt: {state['prompt']}\nStoryboard: {json.dumps(state['storyboard'])}\n"
                    f"Design: {json.dumps(state['design'])}\n"
                    f"Available assets: {[a['object_key'] for a in state['assets']]}\n"
                    f"Prior repair request: {state.get('repair_context', '')}"), schema=GeneratedScene)
        return {"attempt": attempt, "scene": to_builtins(scene),
                "cancelled": self._cancel_requested(state["generation_id"])}

    def render_preview(self, state: AnimationState) -> AnimationState:
        attempt = state["attempt"]
        scene = convert(state["scene"], GeneratedScene)
        assets = [convert(item, AssetRef) for item in state["assets"]]
        profile = RenderProfile(quality="preview", width=640, height=360, fps=15,
                                max_duration_seconds=state["options"].get("duration_seconds", 60))
        package, manifest = build_scene_package(generation_id=UUID(state["generation_id"]), attempt=attempt,
                                                scene_class=scene.scene_class, source=scene.source,
                                                profile=profile, assets=assets)
        key = f"generations/{state['generation_id']}/attempts/{attempt}/scene.tar.gz"
        digest = package_hash(package)
        self.store.put_bytes(key, package, "application/gzip")
        with SessionLocal() as session:
            generation = self._load(session, state["generation_id"])
            add_event(session, generation, GenerationStatus.VALIDATING,
                      "Submitting validated package", f"validate:{attempt}")
            add_event(session, generation, GenerationStatus.RENDERING_PREVIEW,
                      "Rendering preview", f"preview:{attempt}")
        render = self.render_client.submit_and_wait(
            RenderJobCreate(package_key=key, package_sha256=digest, manifest=manifest), 300,
            lambda: self._cancel_requested(state["generation_id"]))
        if render.status != RenderStatus.COMPLETED:
            repair = f"Render failed: {render.error_code}: {render.error_message}\n{render.logs[-3000:]}"
            self._record_attempt(state, {"attempt": attempt, "render_status": render.status.value,
                                         "error_code": render.error_code, "error_message": render.error_message})
            return {"render": to_builtins(render), "render_failed": True, "repair_context": repair,
                    "package_key": key, "package_sha256": digest}
        return {"render": to_builtins(render), "render_failed": False,
                "package_key": key, "package_sha256": digest,
                "cancelled": self._cancel_requested(state["generation_id"])}

    def critique(self, state: AnimationState) -> AnimationState:
        render = state["render"]
        with SessionLocal() as session:
            add_event(session, self._load(session, state["generation_id"]), GenerationStatus.CRITIQUING,
                      "Critiquing sampled frames", f"critique:{state['attempt']}")
        images = [self.store.get_bytes(item["object_key"]) for item in render["artifacts"]
                  if item["kind"] in {"png", "contact_sheet"} and self.store.exists(item["object_key"])]
        report = self.provider.analyze_images(
            instructions=CRITIC_INSTRUCTIONS,
            prompt=f"Original request: {state['prompt']}\nRender logs: {render['logs'][-2000:]}",
            images=images, schema=CriticReport)
        score = report.aggregate_score
        candidate = {"score": score, "artifacts": render["artifacts"], "critic": to_builtins(report),
                     "package_key": state["package_key"], "package_sha256": state["package_sha256"]}
        best = state.get("best")
        if best is None or score > best["score"]:
            best = candidate
        self._record_attempt(state, {"attempt": state["attempt"], "render_status": render["status"],
                                     "critic": to_builtins(report), "score": score})
        with SessionLocal() as session:
            generation = self._load(session, state["generation_id"])
            generation.critic_score = score
            session.commit()
        accepted = report.approved and not report.blocking_issues and score >= self.settings.critic_threshold
        repair = json.dumps({"issues": report.blocking_issues, "instructions": report.repair_instructions})
        return {"critic": to_builtins(report), "score": score, "best": best,
                "accepted": accepted, "repair_context": repair}

    def prepare_repair(self, state: AnimationState) -> AnimationState:
        with SessionLocal() as session:
            add_event(session, self._load(session, state["generation_id"]), GenerationStatus.REPAIRING,
                      "Preparing targeted repair", f"repair:{state['attempt']}")
        return {}

    def render_final(self, state: AnimationState) -> AnimationState:
        scene = convert(state["scene"], GeneratedScene)
        assets = [convert(item, AssetRef) for item in state["assets"]]
        profile = RenderProfile(quality="final", width=1920, height=1080,
                                fps=state["options"].get("fps", 30),
                                max_duration_seconds=state["options"].get("duration_seconds", 60))
        package, manifest = build_scene_package(generation_id=UUID(state["generation_id"]),
                                                attempt=state["attempt"], scene_class=scene.scene_class,
                                                source=scene.source, profile=profile, assets=assets)
        key = f"generations/{state['generation_id']}/final/scene.tar.gz"
        digest = package_hash(package)
        self.store.put_bytes(key, package, "application/gzip")
        with SessionLocal() as session:
            add_event(session, self._load(session, state["generation_id"]),
                      GenerationStatus.RENDERING_FINAL, "Rendering final 1080p output", "final")
        final = self.render_client.submit_and_wait(
            RenderJobCreate(package_key=key, package_sha256=digest, manifest=manifest), 900,
            lambda: self._cancel_requested(state["generation_id"]))
        if final.status != RenderStatus.COMPLETED:
            raise RuntimeError(f"final render failed: {final.error_message}")
        with SessionLocal() as session:
            generation = self._load(session, state["generation_id"])
            generation.artifacts = [to_builtins(item) for item in final.artifacts]
            self._attach_diagnostics(generation, key, digest)
            add_event(session, generation, GenerationStatus.COMPLETED, "Generation completed", "complete")
        return {}

    def complete_with_warnings(self, state: AnimationState) -> AnimationState:
        best = state.get("best")
        if best is None:
            raise RuntimeError("all render attempts failed")
        with SessionLocal() as session:
            generation = self._load(session, state["generation_id"])
            generation.artifacts = best["artifacts"]
            generation.critic_score = best["score"]
            generation.warning = "Automatic repair budget exhausted; returning the best preview"
            self._attach_diagnostics(generation, best["package_key"], best["package_sha256"])
            add_event(session, generation, GenerationStatus.COMPLETED_WITH_WARNINGS,
                      "Repair budget exhausted; best result selected", "complete-warning")
        return {}

    def _record_attempt(self, state: AnimationState, item: dict[str, Any]) -> None:
        with SessionLocal() as session:
            generation = self._load(session, state["generation_id"])
            data = dict(generation.workflow_data or {})
            attempts = list(data.get("attempts", []))
            if not any(value.get("attempt") == item["attempt"] for value in attempts):
                attempts.append(item)
            data["attempts"] = attempts
            generation.workflow_data = data
            session.commit()

    def _attach_diagnostics(self, generation: Generation, source_key: str, source_sha256: str) -> None:
        diagnostics = json.dumps({"generation_id": generation.id, "attempt": generation.attempt,
                                  "critic_score": generation.critic_score, "warning": generation.warning,
                                  "workflow": generation.workflow_data}, indent=2, default=str).encode()
        key = f"generations/{generation.id}/diagnostics.json"
        self.store.put_bytes(key, diagnostics, "application/json")
        generation.artifacts = [*(generation.artifacts or []),
            {"kind": "source", "object_key": source_key, "sha256": source_sha256,
             "size_bytes": None, "download_url": None},
            {"kind": "diagnostics", "object_key": key, "sha256": package_hash(diagnostics),
             "size_bytes": len(diagnostics), "download_url": None}]

    @staticmethod
    def route_cancel(state: AnimationState) -> Literal["continue", "cancel"]:
        return "cancel" if state.get("cancelled") else "continue"

    @staticmethod
    def route_after_preview(state: AnimationState) -> Literal["critique", "repair", "exhausted", "cancel"]:
        if state.get("cancelled"):
            return "cancel"
        if not state.get("render_failed"):
            return "critique"
        return "repair" if state["attempt"] < state["max_attempts"] else "exhausted"

    @staticmethod
    def route_after_critique(state: AnimationState) -> Literal["final", "repair", "exhausted"]:
        if state.get("accepted"):
            return "final"
        return "repair" if state["attempt"] < state["max_attempts"] else "exhausted"

    def build(self, checkpointer: Any):
        graph = StateGraph(AnimationState)
        retry = RetryPolicy(max_attempts=3, initial_interval=1.0, backoff_factor=2.0)
        graph.add_node("hydrate", self.hydrate)
        graph.add_node("plan", self.plan, retry_policy=retry)
        graph.add_node("design", self.design, retry_policy=retry)
        graph.add_node("generate_scene", self.generate_scene, retry_policy=retry)
        graph.add_node("render_preview", self.render_preview)
        graph.add_node("critique", self.critique, retry_policy=retry)
        graph.add_node("prepare_repair", self.prepare_repair)
        graph.add_node("render_final", self.render_final)
        graph.add_node("complete_with_warnings", self.complete_with_warnings)
        graph.add_node("cancel", lambda state: {})
        graph.add_edge(START, "hydrate")
        graph.add_conditional_edges("hydrate", self.route_cancel, {"continue": "plan", "cancel": "cancel"})
        graph.add_conditional_edges("plan", self.route_cancel, {"continue": "design", "cancel": "cancel"})
        graph.add_conditional_edges("design", self.route_cancel, {"continue": "generate_scene", "cancel": "cancel"})
        graph.add_conditional_edges("generate_scene", self.route_cancel,
                                    {"continue": "render_preview", "cancel": "cancel"})
        graph.add_conditional_edges("render_preview", self.route_after_preview,
                                    {"critique": "critique", "repair": "prepare_repair",
                                     "exhausted": "complete_with_warnings", "cancel": "cancel"})
        graph.add_conditional_edges("critique", self.route_after_critique,
                                    {"final": "render_final", "repair": "prepare_repair",
                                     "exhausted": "complete_with_warnings"})
        graph.add_edge("prepare_repair", "generate_scene")
        graph.add_edge("render_final", END)
        graph.add_edge("complete_with_warnings", END)
        graph.add_edge("cancel", END)
        return graph.compile(checkpointer=checkpointer)

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
                    add_event(session, generation, GenerationStatus.FAILED, "Workflow failed", "failed")

    def _run(self, generation_id: str) -> None:
        context = nullcontext(self.checkpointer or InMemorySaver())
        with context as checkpointer:
            graph = self.build(checkpointer)
            config = {"configurable": {"thread_id": generation_id}, "recursion_limit": 50}
            snapshot = graph.get_state(config)
            graph.invoke(None if snapshot.values else {"generation_id": generation_id}, config=config)


WorkflowEngine = AnimationGraph


def run_generation(generation_id: str) -> None:
    AnimationGraph().run(generation_id)

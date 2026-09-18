from __future__ import annotations

import base64
from abc import ABC, abstractmethod
from typing import TypeVar

from openai import OpenAI
from pydantic import BaseModel

from manim_contracts.models import (
    CriticReport,
    GeneratedScene,
    SceneDesign,
    StoryBeat,
    Storyboard,
)

from .settings import Settings, get_settings

T = TypeVar("T", bound=BaseModel)


class ModelProvider(ABC):
    @abstractmethod
    def generate_structured(
        self, *, stage: str, instructions: str, prompt: str, schema: type[T]
    ) -> T: ...

    @abstractmethod
    def analyze_images(
        self, *, instructions: str, prompt: str, images: list[bytes], schema: type[T]
    ) -> T: ...


class OpenAIProvider(ModelProvider):
    def __init__(self, settings: Settings):
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when MODEL_PROVIDER=openai")
        self.client = OpenAI(api_key=settings.openai_api_key)
        self.text_model = settings.openai_text_model
        self.vision_model = settings.openai_vision_model

    @staticmethod
    def _format(schema: type[T]) -> dict:
        return {
            "type": "json_schema",
            "name": schema.__name__,
            "strict": True,
            "schema": schema.model_json_schema(),
        }

    def generate_structured(
        self, *, stage: str, instructions: str, prompt: str, schema: type[T]
    ) -> T:
        response = self.client.responses.create(
            model=self.text_model,
            instructions=instructions,
            input=prompt,
            text={"format": self._format(schema)},
            store=False,
            metadata={"stage": stage},
        )
        return schema.model_validate_json(response.output_text)

    def analyze_images(
        self, *, instructions: str, prompt: str, images: list[bytes], schema: type[T]
    ) -> T:
        content: list[dict] = [{"type": "input_text", "text": prompt}]
        for raw in images:
            encoded = base64.b64encode(raw).decode()
            content.append(
                {"type": "input_image", "image_url": f"data:image/png;base64,{encoded}", "detail": "high"}
            )
        response = self.client.responses.create(
            model=self.vision_model,
            instructions=instructions,
            input=[{"role": "user", "content": content}],
            text={"format": self._format(schema)},
            store=False,
            metadata={"stage": "critic"},
        )
        return schema.model_validate_json(response.output_text)


class FakeProvider(ModelProvider):
    """Deterministic provider for tests and credential-free local development."""

    def generate_structured(
        self, *, stage: str, instructions: str, prompt: str, schema: type[T]
    ) -> T:
        if schema is Storyboard:
            return Storyboard(
                title="Visual explanation",
                learning_objective=prompt[:160],
                beats=[
                    StoryBeat(title="Introduce", objective="State the idea", duration_seconds=8),
                    StoryBeat(title="Demonstrate", objective="Animate the mechanism", duration_seconds=16),
                    StoryBeat(title="Summarize", objective="Reinforce the intuition", duration_seconds=6),
                ],
            )  # type: ignore[return-value]
        if schema is SceneDesign:
            return SceneDesign(
                visual_style="minimal dark educational",
                background_color="#101820",
                choreography=[
                    "Write the title",
                    "Reveal the key objects progressively",
                    "Transform the diagram into the conclusion",
                ],
                asset_usage=[],
            )  # type: ignore[return-value]
        if schema is GeneratedScene:
            source = '''from manim import *

class GeneratedScene(Scene):
    def construct(self):
        title = Text("Visual Explanation", font_size=52)
        subtitle = Text("Generated safely by the agent pipeline", font_size=28).next_to(title, DOWN)
        self.play(Write(title))
        self.play(FadeIn(subtitle, shift=UP))
        self.wait(1)
        group = VGroup(title, subtitle)
        self.play(group.animate.scale(0.75).to_edge(UP))
        dot = Dot(LEFT * 4, color=BLUE)
        path = NumberLine(x_range=[-4, 4, 1], length=8).shift(DOWN)
        self.play(Create(path), FadeIn(dot))
        self.play(dot.animate.move_to(RIGHT * 4 + DOWN), run_time=3)
        self.wait(1)
'''
            return GeneratedScene(scene_class="GeneratedScene", source=source)  # type: ignore[return-value]
        raise ValueError(f"fake provider does not implement {schema.__name__} for {stage}")

    def analyze_images(
        self, *, instructions: str, prompt: str, images: list[bytes], schema: type[T]
    ) -> T:
        if schema is not CriticReport:
            raise ValueError(f"fake provider does not implement vision schema {schema.__name__}")
        return CriticReport(
            approved=True,
            semantic_alignment=0.9,
            readability=0.9,
            composition=0.9,
            continuity=0.9,
            blocking_issues=[],
            repair_instructions=[],
        )  # type: ignore[return-value]


def get_model_provider(settings: Settings | None = None) -> ModelProvider:
    settings = settings or get_settings()
    if settings.model_provider == "openai":
        return OpenAIProvider(settings)
    if settings.model_provider == "fake":
        return FakeProvider()
    raise ValueError(f"unsupported MODEL_PROVIDER: {settings.model_provider}")

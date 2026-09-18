"""Compatibility entrypoint for the LangGraph-backed animation workflow."""

from .graph import AnimationGraph, WorkflowEngine, run_generation

__all__ = ["AnimationGraph", "WorkflowEngine", "run_generation"]

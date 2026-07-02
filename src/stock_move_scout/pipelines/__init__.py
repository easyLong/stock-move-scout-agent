"""Reusable workflow pipelines.

Pipelines orchestrate existing domain modules. They should stay thin:
no scraping details, no SQL assembly that belongs to data modules, and no UI
formatting. CLI scripts and schedulers can call these functions directly.
"""

from .runner import PipelineResult, StepResult, run_step

__all__ = [
    "PipelineResult",
    "StepResult",
    "run_step",
]

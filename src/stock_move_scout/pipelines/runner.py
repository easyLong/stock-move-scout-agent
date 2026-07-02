from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class StepResult:
    name: str
    ok: bool
    elapsed_seconds: float
    payload: dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass(frozen=True)
class PipelineResult:
    name: str
    ok: bool
    steps: tuple[StepResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "steps": [
                {
                    "name": step.name,
                    "ok": step.ok,
                    "elapsed_seconds": step.elapsed_seconds,
                    "payload": step.payload,
                    "error": step.error,
                }
                for step in self.steps
            ],
        }


def run_step(name: str, fn: Callable[[], dict[str, Any] | None]) -> StepResult:
    started = time.time()
    try:
        payload = fn() or {}
        ok = bool(payload.get("ok", True)) if isinstance(payload, dict) else True
        return StepResult(name=name, ok=ok, elapsed_seconds=round(time.time() - started, 2), payload=payload)
    except Exception as exc:
        return StepResult(
            name=name,
            ok=False,
            elapsed_seconds=round(time.time() - started, 2),
            error=f"{type(exc).__name__}: {exc}",
        )

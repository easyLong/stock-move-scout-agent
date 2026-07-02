from __future__ import annotations

"""Artifact paths and writers for morning reference posts."""

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


__all__ = [
    "MorningMirrorPaths",
    "MorningPostPaths",
    "morning_mirror_paths",
    "morning_post_paths",
    "write_json",
    "write_text_outputs",
]


@dataclass(frozen=True)
class MorningPostPaths:
    output_dir: Path
    trade_date: str

    @property
    def post_path(self) -> Path:
        return self.output_dir / f"morning_reference_{self.trade_date}.txt"

    @property
    def latest_path(self) -> Path:
        return self.output_dir / "morning_reference_latest.txt"

    @property
    def meta_path(self) -> Path:
        return self.output_dir / f"morning_reference_{self.trade_date}.json"

    @property
    def workflow_dir(self) -> Path:
        return self.output_dir / f"morning_reference_{self.trade_date}.workflow"

    @property
    def workflow_path(self) -> Path:
        return self.output_dir / f"morning_reference_{self.trade_date}.workflow.json"


@dataclass(frozen=True)
class MorningMirrorPaths:
    output_dir: Path
    post_path: Path
    latest_path: Path


def morning_post_paths(output_dir: Path, trade_day: date) -> MorningPostPaths:
    return MorningPostPaths(output_dir=output_dir, trade_date=trade_day.strftime("%Y-%m-%d"))


def morning_mirror_paths(mirror_output_dir: Path | None, paths: MorningPostPaths) -> MorningMirrorPaths | None:
    if mirror_output_dir is None:
        return None
    return MorningMirrorPaths(
        output_dir=mirror_output_dir,
        post_path=mirror_output_dir / paths.post_path.name,
        latest_path=mirror_output_dir / paths.latest_path.name,
    )


def write_text_outputs(content: str, paths: MorningPostPaths, mirror: MorningMirrorPaths | None = None) -> dict[str, str]:
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    paths.post_path.write_text(content, encoding="utf-8")
    paths.latest_path.write_text(content, encoding="utf-8")
    result = {
        "output_path": str(paths.post_path),
        "latest_path": str(paths.latest_path),
    }
    if mirror is not None:
        mirror.output_dir.mkdir(parents=True, exist_ok=True)
        mirror.post_path.write_text(content, encoding="utf-8")
        mirror.latest_path.write_text(content, encoding="utf-8")
        result["mirror_output_path"] = str(mirror.post_path)
        result["mirror_latest_path"] = str(mirror.latest_path)
    else:
        result["mirror_output_path"] = ""
        result["mirror_latest_path"] = ""
    return result


def write_json(path: Path, payload: dict[str, Any] | list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

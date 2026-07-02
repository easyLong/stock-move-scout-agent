from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import sys

from stock_move_scout.sources import DATA_SOURCES


COMMANDS = {
    "web": "stock_scout_web.py",
    "scheduler": "stock_scout_task_scheduler.py",
    "worker": "stock_scout_task_scheduler.py",
    "seed-tasks": "stock_scout_task_scheduler.py",
    "scan-window": "windowed_stock_scout_agent.py",
    "judgements": "build_stock_move_judgements.py",
    "anchor-roles": "build_anchor_realtime_roles.py",
    "async-evidence": "summarize_async_evidence.py",
    "rebuild-history": "rebuild_history_week.py",
}


def project_root() -> Path:
    env_root = os.environ.get("STOCK_MOVE_SCOUT_HOME", "").strip()
    if env_root:
        return Path(env_root).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def print_help() -> None:
    commands = "\n".join(f"  {name:<14} -> scripts/{script}" for name, script in sorted(COMMANDS.items()))
    print(
        "stock-move-scout\n\n"
        "Usage:\n"
        "  stock-move-scout <command> [script args...]\n"
        "  stock-move-scout script <script_name.py> [script args...]\n\n"
        "Commands:\n"
        f"{commands}\n\n"
        "Examples:\n"
        "  stock-move-scout web --mysql-enabled --mysql-password <MYSQL_PASSWORD>\n"
        "  stock-move-scout scheduler --mysql-enabled --mysql-password <MYSQL_PASSWORD>\n"
        "  stock-move-scout worker --worker-types hot,warm --mysql-enabled --mysql-password <MYSQL_PASSWORD>\n"
        "  stock-move-scout sources\n"
    )


def script_argv(command: str, rest: list[str]) -> tuple[str, list[str]]:
    if command == "script":
        if not rest:
            raise SystemExit("missing script name")
        return rest[0], rest[1:]
    if command not in COMMANDS:
        raise SystemExit(f"unknown command: {command}")
    args = list(rest)
    if command == "scheduler":
        args = ["--mode", "scheduler", *args]
    elif command == "worker":
        args = ["--mode", "worker", *args]
    elif command == "seed-tasks":
        args = ["--mode", "seed", *args]
    return COMMANDS[command], args


def run_script(script_name: str, args: list[str]) -> int:
    root = project_root()
    script_path = root / "scripts" / script_name
    if not script_path.exists():
        raise SystemExit(f"script not found: {script_path}")
    sys.path.insert(0, str(script_path.parent))
    sys.argv = [str(script_path), *args]
    runpy.run_path(str(script_path), run_name="__main__")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"-h", "--help", "help"}:
        print_help()
        return 0
    if argv[0] == "sources":
        if "--json" in argv[1:]:
            print(
                json.dumps(
                    [
                        {
                            "source_id": source.source_id,
                            "tier": source.tier,
                            "owner_layer": source.owner_layer,
                            "refresh": source.refresh,
                            "task_kinds": list(source.task_kinds),
                            "batched_task_kinds": list(source.batched_task_kinds),
                            "scripts": list(source.scripts),
                            "output_tables": list(source.output_tables),
                            "description": source.description,
                        }
                        for source in DATA_SOURCES.values()
                    ],
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        for source in DATA_SOURCES.values():
            batched = ",".join(source.batched_task_kinds) or "-"
            print(
                f"{source.source_id}\t{source.tier}\t{source.refresh}\t"
                f"batched={batched}\ttables={','.join(source.output_tables)}"
            )
        return 0
    script_name, args = script_argv(argv[0], argv[1:])
    return run_script(script_name, args)


if __name__ == "__main__":
    raise SystemExit(main())

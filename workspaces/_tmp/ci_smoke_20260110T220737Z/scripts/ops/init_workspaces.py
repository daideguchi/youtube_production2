#!/usr/bin/env python3
"""
Initialize `workspaces/` layout (idempotent).

This script exists to prevent "迷いどころ" / path drift:
  - ensures minimal `workspaces/**` directories exist
  - creates README/.gitignore/.gitkeep only when missing
  - refuses to proceed if repo-root has layout drift (unexpected dirs/symlinks)

Usage:
  python3 scripts/ops/init_workspaces.py          # dry-run
  python3 scripts/ops/init_workspaces.py --run    # apply
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from _bootstrap import bootstrap


@dataclass(frozen=True)
class WantedFile:
    path: Path
    content: str
    mode: str  # "text" | "touch"


def _ensure_dir(path: Path, *, run: bool) -> None:
    if path.exists():
        if not path.is_dir():
            raise RuntimeError(f"Expected directory but found file: {path}")
        return
    if run:
        path.mkdir(parents=True, exist_ok=True)


def _ensure_file(wanted: WantedFile, *, run: bool) -> None:
    if wanted.path.exists():
        return
    _ensure_dir(wanted.path.parent, run=run)
    if not run:
        return
    if wanted.mode == "touch":
        wanted.path.write_bytes(b"")
        return
    wanted.path.write_text(wanted.content, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Initialize workspaces/ layout (no legacy alias symlinks).")
    ap.add_argument("--run", action="store_true", help="Actually write files (default: dry-run).")
    args = ap.parse_args()
    run = bool(args.run)

    bootstrap(load_env=False)
    from factory_common.paths import repo_root as _repo_root  # noqa: WPS433 (late import)
    from factory_common.paths import workspace_root as _workspace_root  # noqa: WPS433 (late import)
    from factory_common.repo_layout import unexpected_repo_root_entries  # noqa: WPS433 (late import)

    root = _repo_root()
    ws = _workspace_root()

    unexpected = unexpected_repo_root_entries(root)
    if unexpected:
        joined = "\n".join(f"  - {p.relative_to(root)}" for p in unexpected)
        raise RuntimeError(
            "Unexpected repo-root directories/symlinks exist (layout drift).\n"
            "Clean them up (archive-first if tracked) and retry.\n"
            f"{joined}"
        )

    if ws.exists() and ws.is_symlink():
        raise RuntimeError(f"workspaces/ must be a real directory, not a symlink: {ws}")

    _ensure_dir(ws, run=run)
    _ensure_dir(ws / "audio", run=run)
    _ensure_dir(ws / "logs", run=run)
    _ensure_dir(ws / "scripts", run=run)
    _ensure_dir(ws / "video" / "input", run=run)
    _ensure_dir(ws / "video" / "runs", run=run)

    wanted_files: list[WantedFile] = [
        WantedFile(
            path=ws / "README.md",
            mode="text",
            content="# workspaces/\n\nSoT + generated artifacts live here. Do not commit large outputs.\n",
        ),
        WantedFile(
            path=ws / ".gitignore",
            mode="text",
            content=(
                "# Generated artifacts under workspaces/ (do not commit)\n"
                "audio/**\n"
                "logs/**\n"
                "scripts/**\n"
                "video/input/**\n"
                "video/runs/**\n"
                "\n"
                "# Keep docs (tracked)\n"
                "!README.md\n"
                "!audio/README.md\n"
                "!logs/README.md\n"
                "!scripts/README.md\n"
                "!video/README.md\n"
                "!video/input/.gitkeep\n"
                "!video/runs/.gitkeep\n"
            ),
        ),
        WantedFile(
            path=ws / "audio" / "README.md",
            mode="text",
            content="# workspaces/audio/\n\nGenerated audio artifacts (final wav/srt/log). Do not commit.\n",
        ),
        WantedFile(
            path=ws / "logs" / "README.md",
            mode="text",
            content="# workspaces/logs/\n\nRuntime logs, agent queues, and regression outputs. Do not commit.\n",
        ),
        WantedFile(
            path=ws / "scripts" / "README.md",
            mode="text",
            content="# workspaces/scripts/\n\nGenerated script artifacts (SoT) and intermediate states. Do not commit.\n",
        ),
        WantedFile(
            path=ws / "video" / "README.md",
            mode="text",
            content="# workspaces/video/\n\nGenerated video inputs and runs (CapCut/Remotion). Do not commit.\n",
        ),
        WantedFile(path=ws / "video" / "input" / ".gitkeep", mode="touch", content=""),
        WantedFile(path=ws / "video" / "runs" / ".gitkeep", mode="touch", content=""),
    ]

    for wf in wanted_files:
        _ensure_file(wf, run=run)

    if not run:
        print("[dry-run] add --run to apply (creates only missing files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

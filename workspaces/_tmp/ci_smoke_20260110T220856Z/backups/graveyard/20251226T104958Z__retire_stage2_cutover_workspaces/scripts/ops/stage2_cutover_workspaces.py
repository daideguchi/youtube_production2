from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

def _find_repo_root(start: Path) -> Path:
    cur = start if start.is_dir() else start.parent
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate.resolve()
    return cur.resolve()


PROJECT_ROOT = _find_repo_root(Path(__file__).resolve())
PACKAGES_ROOT = PROJECT_ROOT / "packages"
for p in (PROJECT_ROOT, PACKAGES_ROOT):
    p_str = str(p)
    if p_str not in sys.path:
        sys.path.insert(0, p_str)

from factory_common.paths import repo_root, workspace_root


@dataclass(frozen=True)
class CutoverTarget:
    name: str
    ws_path: Path
    legacy_path: Path
    ignore_globs: tuple[str, ...]


def _relpath(from_dir: Path, to_path: Path) -> str:
    return os.path.relpath(to_path, start=from_dir)


def _fmt(p: Path) -> str:
    try:
        return str(p)
    except Exception:
        return repr(p)


def _ensure_parent(path: Path, run: bool) -> None:
    if path.parent.exists():
        return
    if run:
        path.parent.mkdir(parents=True, exist_ok=True)


def _rename(src: Path, dst: Path, run: bool) -> None:
    if run:
        src.rename(dst)


def _symlink(link_path: Path, target_path: Path, run: bool) -> None:
    _ensure_parent(link_path, run=run)
    rel = _relpath(link_path.parent, target_path)
    if run:
        link_path.symlink_to(rel)


def _write_text(path: Path, content: str, run: bool) -> None:
    _ensure_parent(path, run=run)
    if run:
        path.write_text(content, encoding="utf-8")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _ensure_workspace_gitignore(targets: list[CutoverTarget], run: bool) -> None:
    ws_gitignore = workspace_root() / ".gitignore"
    wanted_lines: list[str] = [
        "# Generated artifacts under workspaces/ (do not commit)",
    ]
    for target in targets:
        for glob in target.ignore_globs:
            wanted_lines.append(glob)
    wanted_lines += [
        "",
        "# Keep docs (tracked)",
        "!README.md",
        "!audio/README.md",
        "!logs/README.md",
        "!scripts/README.md",
        "!video/README.md",
        "!video/input/.gitkeep",
        "!video/runs/.gitkeep",
    ]
    wanted = "\n".join(wanted_lines).rstrip() + "\n"

    if ws_gitignore.exists():
        existing = _read_text(ws_gitignore)
        if wanted.strip() in existing:
            return
        merged = existing.rstrip() + "\n\n" + wanted
        _write_text(ws_gitignore, merged, run=run)
        return

    _write_text(ws_gitignore, wanted, run=run)


def _ensure_workspace_readmes(run: bool) -> None:
    ws = workspace_root()
    audio_readme = ws / "audio" / "README.md"
    logs_readme = ws / "logs" / "README.md"
    scripts_readme = ws / "scripts" / "README.md"
    video_readme = ws / "video" / "README.md"

    if not audio_readme.exists():
        _write_text(
            audio_readme,
            "# workspaces/audio/\n\nGenerated audio artifacts (final wav/srt/log). Do not commit.\n",
            run=run,
        )
    if not logs_readme.exists():
        _write_text(
            logs_readme,
            "# workspaces/logs/\n\nRuntime logs, agent queues, and regression outputs. Do not commit.\n",
            run=run,
        )
    if not scripts_readme.exists():
        _write_text(
            scripts_readme,
            "# workspaces/scripts/\n\nGenerated script artifacts (SoT) and intermediate states. Do not commit.\n",
            run=run,
        )
    if not video_readme.exists():
        _write_text(
            video_readme,
            "# workspaces/video/\n\nGenerated video inputs and runs (CapCut/Remotion). Do not commit.\n",
            run=run,
        )


def _is_done(target: CutoverTarget) -> bool:
    ws_ok = target.ws_path.exists() and not target.ws_path.is_symlink()
    legacy_ok = target.legacy_path.is_symlink()
    return ws_ok and legacy_ok


def _is_ready_for_cutover(target: CutoverTarget) -> bool:
    ws_is_symlink = target.ws_path.is_symlink()
    legacy_is_dir = target.legacy_path.exists() and target.legacy_path.is_dir() and not target.legacy_path.is_symlink()
    return ws_is_symlink and legacy_is_dir


def cutover(target: CutoverTarget, run: bool) -> None:
    if _is_done(target):
        print(f"[skip] {target.name}: already cut over")
        return

    if not _is_ready_for_cutover(target):
        raise RuntimeError(
            f"{target.name}: unexpected state.\n"
            f"  ws_path: {_fmt(target.ws_path)} (exists={target.ws_path.exists()} symlink={target.ws_path.is_symlink()})\n"
            f"  legacy_path: {_fmt(target.legacy_path)} (exists={target.legacy_path.exists()} symlink={target.legacy_path.is_symlink()})"
        )

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    ws_backup = target.ws_path.with_name(f"{target.ws_path.name}.__symlink_backup_{ts}")

    print(f"[plan] {target.name}")
    print(f"  1) mv {target.ws_path} -> {ws_backup}")
    print(f"  2) mv {target.legacy_path} -> {target.ws_path}")
    print(f"  3) ln -s {target.ws_path} -> {target.legacy_path}")
    print(f"  4) rm {ws_backup}")

    _rename(target.ws_path, ws_backup, run=run)
    _rename(target.legacy_path, target.ws_path, run=run)
    _symlink(target.legacy_path, target.ws_path, run=run)
    if run:
        ws_backup.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stage2 cutover: move large artifacts into workspaces/ and keep legacy paths as symlinks."
    )
    parser.add_argument("--run", action="store_true", help="Actually execute (default: dry-run).")
    parser.add_argument(
        "--only",
        action="append",
        choices=["audio", "logs", "scripts", "video-input", "video-runs"],
        help="Restrict to a subset (repeatable).",
    )
    args = parser.parse_args()
    run = bool(args.run)

    root = repo_root()
    ws = workspace_root()
    selected = set(args.only or ["audio", "logs", "scripts", "video-input", "video-runs"])

    targets: list[CutoverTarget] = []
    if "audio" in selected:
        targets.append(
            CutoverTarget(
                name="audio",
                ws_path=ws / "audio",
                legacy_path=root / "audio_tts_v2" / "artifacts",
                ignore_globs=("audio/**",),
            )
        )
    if "logs" in selected:
        targets.append(
            CutoverTarget(
                name="logs",
                ws_path=ws / "logs",
                legacy_path=root / "logs",
                ignore_globs=("logs/**",),
            )
        )
    if "scripts" in selected:
        targets.append(
            CutoverTarget(
                name="scripts",
                ws_path=ws / "scripts",
                legacy_path=root / "script_pipeline" / "data",
                ignore_globs=("scripts/**",),
            )
        )
    if "video-input" in selected:
        targets.append(
            CutoverTarget(
                name="video-input",
                ws_path=ws / "video" / "input",
                legacy_path=root / "commentary_02_srt2images_timeline" / "input",
                ignore_globs=("video/input/**",),
            )
        )
    if "video-runs" in selected:
        targets.append(
            CutoverTarget(
                name="video-runs",
                ws_path=ws / "video" / "runs",
                legacy_path=root / "commentary_02_srt2images_timeline" / "output",
                ignore_globs=("video/runs/**",),
            )
        )

    _ensure_workspace_gitignore(targets, run=run)
    _ensure_workspace_readmes(run=run)

    for target in targets:
        cutover(target, run=run)

    if not run:
        print("[dry-run] add --run to execute")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Copy image_cues.json from each output run directory into its CapCut draft.

This backfills older drafts so the UI can always read the original generation
prompts (cue_info.prompt) even if the draft was created before we started
copying the metadata automatically.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Optional


def _bootstrap_repo_root() -> Path:
    start = Path(__file__).resolve()
    cur = start if start.is_dir() else start.parent
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
    return cur


_BOOTSTRAP_REPO = _bootstrap_repo_root()
_PACKAGES_ROOT = _BOOTSTRAP_REPO / "packages"
for p in (_BOOTSTRAP_REPO, _PACKAGES_ROOT):
    p_str = str(p)
    if p_str not in sys.path:
        sys.path.insert(0, p_str)

from factory_common.paths import video_pkg_root, video_runs_root  # noqa: E402

PROJECT_ROOT = video_pkg_root()
DEFAULT_OUTPUT_ROOT = video_runs_root()
DEFAULT_CAPCUT_ROOT = Path.home() / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"


def resolve_draft_dir(run_dir: Path, capcut_root: Path) -> Optional[Path]:
    """Resolve the CapCut draft folder for a given run directory."""
    link = run_dir / "capcut_draft"
    if link.exists():
        try:
            target = link.resolve()
            if target.exists():
                return target
        except Exception:
            pass

    info_path = run_dir / "capcut_draft_info.json"
    if info_path.exists():
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
            draft_path = info.get("draft_path")
            if draft_path:
                candidate = Path(draft_path).expanduser()
                if candidate.exists():
                    return candidate
        except Exception:
            pass

    candidate = capcut_root / run_dir.name
    if candidate.exists():
        return candidate
    return None


def copy_image_cues(run_dir: Path, draft_dir: Path, dry_run: bool = False) -> str:
    src = run_dir / "image_cues.json"
    if not src.exists():
        return "missing_source"

    dest = draft_dir / "image_cues.json"

    try:
        src_stat = src.stat()
        if dest.exists():
            dest_stat = dest.stat()
            if dest_stat.st_size == src_stat.st_size and dest_stat.st_mtime >= src_stat.st_mtime:
                return "up_to_date"
    except FileNotFoundError:
        pass

    if dry_run:
        action = "create" if not dest.exists() else "update"
        print(f"[DRY-RUN] {action}: {src} -> {dest}")
        return "dry_run"

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return "copied"


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync image_cues.json into each CapCut draft folder.")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="srt2images output root (contains run directories)")
    parser.add_argument("--capcut-root", default=str(DEFAULT_CAPCUT_ROOT), help="CapCut drafts root directory")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be copied without writing files")
    args = parser.parse_args()

    output_root = Path(args.output_root).expanduser().resolve()
    capcut_root = Path(args.capcut_root).expanduser().resolve()

    if not output_root.exists():
        raise SystemExit(f"Output root not found: {output_root}")

    summary = {
        "copied": 0,
        "dry_run": 0,
        "up_to_date": 0,
        "missing_source": 0,
        "missing_draft": 0,
    }

    run_dirs = sorted([p for p in output_root.iterdir() if p.is_dir()])
    if not run_dirs:
        print(f"No run directories found under {output_root}")
        return

    for run_dir in run_dirs:
        cues_src = run_dir / "image_cues.json"
        if not cues_src.exists():
            summary["missing_source"] += 1
            continue

        draft_dir = resolve_draft_dir(run_dir, capcut_root)
        if not draft_dir:
            print(f"⚠️  Draft folder not found for run: {run_dir.name}")
            summary["missing_draft"] += 1
            continue

        result = copy_image_cues(run_dir, draft_dir, dry_run=args.dry_run)
        summary[result] = summary.get(result, 0) + 1
        if result == "copied":
            print(f"✅ Copied image_cues.json to {draft_dir}")
        elif result == "up_to_date":
            pass  # Already synced; no noise
        elif result == "dry_run":
            pass

    print("---- Summary ----")
    for key in ("copied", "up_to_date", "dry_run", "missing_source", "missing_draft"):
        print(f"{key:>15}: {summary.get(key, 0)}")


if __name__ == "__main__":
    main()

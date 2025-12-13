#!/usr/bin/env python3
"""
Rebuild CapCut drafts for a channel/video range using:
  - Existing run_dir assets (image_cues.json + images/)
  - Final TTS artifacts as SoT (audio_tts_v2/artifacts/final/<CH>/<NNN>/<CH>-<NNN>.srt)

Defaults are "no LLM / no image generation":
  - Uses --resume
  - Uses --nanobanana none

Example:
  cd commentary_02_srt2images_timeline
  python3 tools/rebuild_capcut_drafts_range.py --channel CH05 --videos 001-030
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

def _bootstrap_repo_root() -> Path:
    start = Path(__file__).resolve()
    cur = start if start.is_dir() else start.parent
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
    return cur


_BOOTSTRAP_REPO = _bootstrap_repo_root()
if str(_BOOTSTRAP_REPO) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_REPO))

from factory_common.paths import audio_artifacts_root, video_pkg_root, video_runs_root  # noqa: E402

PROJECT_ROOT = video_pkg_root()
OUTPUT_ROOT = video_runs_root()
DEFAULT_DRAFT_ROOT = Path.home() / "Movies/CapCut/User Data/Projects/com.lveditor.draft"


def _z3(x: str | int) -> str:
    return str(int(x)).zfill(3)


def _parse_videos(spec: str) -> list[str]:
    """
    Parse video spec like:
      - "1-30"
      - "001-030"
      - "001,002,010-012"
    Returns sorted unique 3-digit strings.
    """
    spec = (spec or "").strip()
    if not spec:
        raise ValueError("--videos is required")

    out: set[str] = set()
    for part in [p.strip() for p in spec.split(",") if p.strip()]:
        if "-" in part:
            a, b = [x.strip() for x in part.split("-", 1)]
            start = int(a)
            end = int(b)
            if end < start:
                raise ValueError(f"Invalid range: {part}")
            for n in range(start, end + 1):
                out.add(_z3(n))
        else:
            out.add(_z3(part))
    return sorted(out)


def _pick_run_name(channel: str, video: str) -> str:
    """
    Prefer <CH>-<NNN>_redo if it exists, else pick newest matching prefix.
    """
    episode = f"{channel}-{video}"
    out_root = OUTPUT_ROOT
    preferred = f"{episode}_redo"
    if (out_root / preferred).is_dir():
        return preferred

    candidates = list(out_root.glob(f"{episode}_*"))
    if not candidates:
        # Also accept legacy with underscore: CH05_001
        candidates = list(out_root.glob(f"{channel}_{int(video)}*"))
    if not candidates:
        raise FileNotFoundError(f"run_dir not found for {episode} under {out_root}")

    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0].name


def _final_srt_path(channel: str, video: str) -> Path:
    episode = f"{channel}-{video}"
    return audio_artifacts_root() / "final" / channel / video / f"{episode}.srt"


def _validate_run_dir(run_dir: Path) -> None:
    cues = run_dir / "image_cues.json"
    imgs = run_dir / "images"
    if not cues.exists():
        raise FileNotFoundError(f"Missing image_cues.json: {cues}")
    if not imgs.is_dir():
        raise FileNotFoundError(f"Missing images/: {imgs}")


def _run(cmd: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    proc = subprocess.run(cmd, cwd=str(cwd), env=env)
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True, help="Channel ID (e.g., CH05)")
    ap.add_argument("--videos", required=True, help="Video spec (e.g., 001-030 or 1-30 or 001,002,010-012)")
    ap.add_argument("--draft-root", default=str(DEFAULT_DRAFT_ROOT), help="CapCut draft root (default: macOS CapCut)")
    ap.add_argument("--timeout-ms", type=int, default=0, help="Timeout per episode (ms) (default: 0 = unlimited)")
    args = ap.parse_args()

    channel = args.channel.upper().strip()
    if not re.fullmatch(r"CH\d{2}", channel):
        raise SystemExit(f"Invalid --channel: {args.channel}")

    videos = _parse_videos(args.videos)
    env = os.environ.copy()

    for idx, video in enumerate(videos, start=1):
        episode = f"{channel}-{video}"
        run_name = _pick_run_name(channel, video)
        run_dir = OUTPUT_ROOT / run_name
        _validate_run_dir(run_dir)

        srt_path = _final_srt_path(channel, video)
        if not srt_path.exists():
            raise SystemExit(f"Missing final SRT (SoT): {srt_path}")

        cmd = [
            sys.executable,
            "tools/auto_capcut_run.py",
            "--channel",
            channel,
            "--srt",
            str(srt_path),
            "--run-name",
            run_name,
            "--resume",
            "--nanobanana",
            "none",
            "--belt-mode",
            "existing",
            "--draft-root",
            str(Path(args.draft_root).expanduser().resolve()),
            "--timeout-ms",
            str(args.timeout_ms),
        ]
        print(f"[{idx}/{len(videos)}] {episode} → run_dir={run_name}")
        _run(cmd, cwd=PROJECT_ROOT, env=env)


if __name__ == "__main__":
    main()

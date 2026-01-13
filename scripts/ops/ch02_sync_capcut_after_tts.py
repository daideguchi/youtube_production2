#!/usr/bin/env python3
"""
CH02: After regenerating TTS (workspaces/audio/final), retime existing run_dir + CapCut draft.

Goal:
- Keep A-text untouched.
- Regenerate audio/SRT/log via TTS first (separately).
- Then, for existing run_dirs, sync timelines so CapCut stays aligned:
  1) align_run_dir_to_tts_final (updates run_dir SRT + cue timings + timeline_manifest)
  2) patch_draft_images_from_cues (retime srt2images tracks)
  3) patch_draft_audio_subtitles_from_manifest (swap audio + subtitles)

This script is deterministic and does NOT generate images.

Examples:
  python3 scripts/ops/ch02_sync_capcut_after_tts.py --min-video 42 --max-video 82
  python3 scripts/ops/ch02_sync_capcut_after_tts.py --channel CH02 --prefer-substr mix433_20260106 --min-video 42 --max-video 82
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from scripts.ops._bootstrap import bootstrap


@dataclass(frozen=True)
class Result:
    video: str
    run_dir: Optional[Path]
    status: str
    detail: str


def _iter_videos(min_id: int, max_id: int) -> Iterable[str]:
    for n in range(min_id, max_id + 1):
        yield f"{n:03d}"


def _v_suffix_num(name: str) -> int:
    m = re.search(r"_v(\\d+)$", name)
    return int(m.group(1)) if m else -1


def _read_cues_count(run_dir: Path) -> int:
    cues_path = run_dir / "image_cues.json"
    if not cues_path.exists():
        return -1
    try:
        import json

        payload = json.loads(cues_path.read_text(encoding="utf-8"))
        return len(payload.get("cues") or [])
    except Exception:
        return -1


def _draft_srt2images_segment_counts(draft_dir: Path) -> list[int]:
    dc_path = draft_dir / "draft_content.json"
    if not dc_path.exists():
        return []
    try:
        import json

        dc = json.loads(dc_path.read_text(encoding="utf-8"))
        tracks = list(dc.get("tracks") or [])
        out: list[int] = []
        for t in tracks:
            if not isinstance(t, dict):
                continue
            name = str(t.get("name") or "")
            if not name.startswith("srt2images_"):
                continue
            out.append(len(t.get("segments") or []))
        return out
    except Exception:
        return []


def _pick_run_dir(*, root: Path, channel: str, video: str, prefer_substr: str) -> Optional[Path]:
    token = f"{channel}-{video}"
    candidates = [p for p in root.iterdir() if p.is_dir() and token in p.name]
    if not candidates:
        return None

    def key(p: Path) -> tuple[int, int, int, int, int, float, str]:
        # Prefer:
        #  1) Cues count matches draft srt2images segment count (most important)
        #  2) CapCut draft resolvable (so we can actually patch timelines)
        #  3) run_dir name containing prefer_substr
        #  4) higher _vN suffix when present
        #  5) newer mtime
        #  6) stable name tiebreak
        draft = _resolve_capcut_draft(run_dir=p, apply_fixes=False)
        resolvable = 1 if (draft and draft.exists()) else 0
        cues_count = _read_cues_count(p)
        seg_counts = _draft_srt2images_segment_counts(draft) if draft else []
        if cues_count >= 0 and seg_counts:
            delta = min(abs(int(c) - int(cues_count)) for c in seg_counts)
        else:
            delta = 10_000
        match = 1 if delta == 0 else 0
        preferred = 1 if (prefer_substr and prefer_substr in p.name) else 0
        vnum = _v_suffix_num(p.name)
        try:
            mtime = float(p.stat().st_mtime)
        except Exception:
            mtime = 0.0
        # Smaller delta is better; invert so higher is better for reverse sort.
        return (match, resolvable, -delta, preferred, vnum, mtime, p.name)

    return sorted(candidates, key=key, reverse=True)[0]


def _resolve_capcut_draft(*, run_dir: Path, apply_fixes: bool) -> Optional[Path]:
    # Reuse the proven resolver (fixes broken symlink like "...(1)" and updates capcut_draft_info.json).
    from video_pipeline.tools.audit_fix_drafts import _resolve_capcut_draft_dir

    return _resolve_capcut_draft_dir(run_dir=run_dir, apply_fixes=bool(apply_fixes))


def _run_module(module: str, args: list[str], *, dry_run: bool) -> tuple[bool, str]:
    cmd = [sys.executable, "-m", module, *args]
    if dry_run:
        return True, f"DRY_RUN: {' '.join(cmd)}"
    try:
        subprocess.run(cmd, check=True)
        return True, "ok"
    except subprocess.CalledProcessError as e:
        return False, f"exit={e.returncode}"


def main() -> None:
    bootstrap(load_env=True)
    from factory_common.paths import audio_final_dir, video_runs_root

    ap = argparse.ArgumentParser(description="CH02: sync CapCut draft timings after TTS regeneration.")
    ap.add_argument("--channel", default="CH02")
    ap.add_argument("--min-video", "--min-id", type=int, required=True)
    ap.add_argument("--max-video", "--max-id", type=int, required=True)
    ap.add_argument(
        "--prefer-substr",
        default="mix433_20260106",
        help="Preferred substring in run_dir name (default: mix433_20260106).",
    )
    ap.add_argument(
        "--tolerance-sec",
        type=float,
        default=3.0,
        help="Allowed wav/srt end mismatch tolerance for timeline validation (default: 3.0).",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    channel = str(args.channel).strip()
    root = video_runs_root()
    results: list[Result] = []

    for vid in _iter_videos(int(args.min_video), int(args.max_video)):
        final_dir = audio_final_dir(channel, vid)
        if not (final_dir / f"{channel}-{vid}.wav").exists():
            results.append(Result(video=vid, run_dir=None, status="skip", detail="final wav missing"))
            continue

        run_dir = _pick_run_dir(root=root, channel=channel, video=vid, prefer_substr=str(args.prefer_substr))
        if not run_dir:
            results.append(Result(video=vid, run_dir=None, status="skip", detail="run_dir not found"))
            continue

        draft_dir = _resolve_capcut_draft(run_dir=run_dir, apply_fixes=not args.dry_run)
        if not draft_dir or not draft_dir.exists():
            results.append(Result(video=vid, run_dir=run_dir, status="skip", detail="capcut_draft unresolved"))
            continue

        ok, msg = _run_module(
            "video_pipeline.tools.align_run_dir_to_tts_final",
            ["--run", str(run_dir)],
            dry_run=bool(args.dry_run),
        )
        if not ok:
            results.append(Result(video=vid, run_dir=run_dir, status="fail", detail=f"align_run_dir_to_tts_final: {msg}"))
            continue

        ok, msg = _run_module(
            "video_pipeline.tools.patch_draft_images_from_cues",
            ["--run", str(run_dir)],
            dry_run=bool(args.dry_run),
        )
        if not ok:
            results.append(Result(video=vid, run_dir=run_dir, status="fail", detail=f"patch_draft_images_from_cues: {msg}"))
            continue

        ok, msg = _run_module(
            "video_pipeline.tools.patch_draft_audio_subtitles_from_manifest",
            [
                "--run",
                str(run_dir),
                "--draft",
                str(draft_dir),
                "--tolerance-sec",
                str(float(args.tolerance_sec)),
            ],
            dry_run=bool(args.dry_run),
        )
        if not ok:
            results.append(
                Result(video=vid, run_dir=run_dir, status="fail", detail=f"patch_draft_audio_subtitles_from_manifest: {msg}")
            )
            continue

        results.append(Result(video=vid, run_dir=run_dir, status="ok", detail="synced"))

    ok_v = [r.video for r in results if r.status == "ok"]
    skip_v = [r.video for r in results if r.status == "skip"]
    fail_v = [r for r in results if r.status == "fail"]

    print(f"[DONE] ok={len(ok_v)} skip={len(skip_v)} fail={len(fail_v)}")
    if fail_v:
        print("[FAIL]")
        for r in fail_v:
            rd = r.run_dir.name if r.run_dir else "-"
            print(f"  - {channel}-{r.video} run={rd}: {r.detail}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()

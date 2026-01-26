#!/usr/bin/env python3
"""
Patch an existing CapCut draft by retiming the injected srt2images_* video track
to match the run_dir's current image_cues.json.

Why:
  - After regenerating TTS and retiming run_dir/image_cues.json (e.g., via
    `align_run_dir_to_tts_final.py`), the CapCut draft's image track timings can
    drift from the updated cues.
  - This tool updates ONLY the srt2images_* track segment timeranges (and speed),
    keeping other tracks intact.

Usage:
  PYTHONPATH=".:packages" python3 -m video_pipeline.tools.patch_draft_images_from_cues \\
    --run workspaces/video/runs/CH04-023_capcut_unpub_noimg_20260106_v1
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

try:
    from video_pipeline.tools._tool_bootstrap import bootstrap as tool_bootstrap
except Exception:
    from _tool_bootstrap import bootstrap as tool_bootstrap  # type: ignore

tool_bootstrap(load_env=False)

from factory_common.path_ref import resolve_path_ref  # noqa: E402
from video_pipeline.tools.capcut_bulk_insert import sync_draft_info_with_content  # noqa: E402


SEC_US = 1_000_000


def _now_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_name(f"{path.name}.tmp.{_now_tag()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def _ms_align(us: int) -> int:
    # CapCut timelines tend to be ms-aligned; keep edits deterministic.
    return max(0, (int(us) // 1000) * 1000)


def _select_srt2images_track(tracks: list[dict[str, Any]], *, run_name: str, cues_count: int) -> dict[str, Any]:
    base = f"srt2images_{run_name}"
    candidates = [
        t
        for t in tracks
        if isinstance(t, dict)
        and (t.get("type") == "video")
        and isinstance(t.get("name"), str)
        and str(t.get("name") or "").startswith("srt2images_")
    ]
    if not candidates:
        raise RuntimeError("No srt2images_* video track found in draft_content.json")

    exact = [t for t in candidates if str(t.get("name") or "") == base]
    if len(exact) == 1:
        return exact[0]

    by_count = [t for t in candidates if len(t.get("segments") or []) == int(cues_count)]
    if len(by_count) == 1:
        return by_count[0]

    # Fall back to the first candidate for the run (stable across most drafts).
    if candidates:
        return candidates[0]

    raise RuntimeError("Could not uniquely select srt2images_* track")


def _cue_timerange_us(cue: dict[str, Any], fps: int) -> tuple[int, int]:
    start_sec = cue.get("start_sec")
    end_sec = cue.get("end_sec")
    if isinstance(start_sec, (int, float)) and isinstance(end_sec, (int, float)):
        start_us = _ms_align(int(round(float(start_sec) * SEC_US)))
        end_us = _ms_align(int(round(float(end_sec) * SEC_US)))
        return start_us, max(end_us, start_us)

    # Fallback: derive from frames
    start_frame = cue.get("start_frame")
    end_frame = cue.get("end_frame")
    if isinstance(start_frame, int) and isinstance(end_frame, int) and fps > 0:
        start_us = _ms_align(int(round(start_frame / fps * SEC_US)))
        end_us = _ms_align(int(round(end_frame / fps * SEC_US)))
        return start_us, max(end_us, start_us)

    raise RuntimeError(f"Cue is missing timing fields: {cue.get('index')}")


def _retime_segments(track: dict[str, Any], cues: list[dict[str, Any]], fps: int) -> None:
    segs = list(track.get("segments") or [])
    if len(segs) != len(cues):
        raise RuntimeError(f"Segment count mismatch: draft={len(segs)} cues={len(cues)}")

    for i, (seg, cue) in enumerate(zip(segs, cues)):
        if not isinstance(seg, dict):
            raise RuntimeError(f"Invalid segment at index {i}")

        start_us, end_us = _cue_timerange_us(cue, fps)
        dur_us = max(0, end_us - start_us)

        tt = seg.get("target_timerange")
        if not isinstance(tt, dict):
            tt = {}
            seg["target_timerange"] = tt
        tt["start"] = int(start_us)
        tt["duration"] = int(dur_us)

        rt = seg.get("render_timerange")
        if isinstance(rt, dict):
            rt["start"] = int(start_us)
            rt["duration"] = int(dur_us)

        # Keep source selection stable; only adjust speed so the chosen source range fills the new target duration.
        st = seg.get("source_timerange")
        if isinstance(st, dict) and "duration" in st and isinstance(st.get("duration"), (int, float)):
            src_dur = float(st.get("duration") or 0.0)
            if src_dur > 0 and dur_us > 0:
                seg["speed"] = float(src_dur) / float(dur_us)

    track["segments"] = segs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="run_dir containing image_cues.json and capcut_draft (symlink or draft_path_ref)")
    args = ap.parse_args()

    run_dir = Path(args.run).expanduser().resolve()
    if not run_dir.exists():
        raise SystemExit(f"run_dir not found: {run_dir}")

    cues_path = run_dir / "image_cues.json"
    if not cues_path.exists():
        raise SystemExit(f"image_cues.json not found: {cues_path}")

    draft_dir: Optional[Path] = None
    draft_link = run_dir / "capcut_draft"
    if draft_link.is_symlink():
        try:
            draft_dir = draft_link.resolve()
        except Exception:
            draft_dir = None
    elif draft_link.exists():
        draft_dir = draft_link

    if draft_dir is None or not draft_dir.exists():
        info_path = run_dir / "capcut_draft_info.json"
        info = {}
        if info_path.exists():
            try:
                info = _read_json(info_path)
            except Exception:
                info = {}
        resolved = resolve_path_ref((info or {}).get("draft_path_ref"))
        if resolved is not None:
            draft_dir = resolved.expanduser().resolve()
        else:
            legacy = str((info or {}).get("draft_path") or "").strip()
            if legacy:
                draft_dir = Path(legacy).expanduser().resolve()

    if draft_dir is None or not draft_dir.exists():
        raise SystemExit(f"capcut_draft not found (symlink/path_ref missing or not accessible): {run_dir}")

    content_path = draft_dir / "draft_content.json"
    info_path = draft_dir / "draft_info.json"
    if not content_path.exists() or not info_path.exists():
        raise SystemExit(f"draft_content.json or draft_info.json missing under: {draft_dir}")

    cues_payload = _read_json(cues_path)
    fps = int(cues_payload.get("fps") or 30)
    cues = list(cues_payload.get("cues") or [])
    if not cues:
        raise SystemExit("image_cues.json has no cues")

    content = _read_json(content_path)
    tracks = list(content.get("tracks") or [])
    if not isinstance(tracks, list) or not tracks:
        raise SystemExit("draft_content.json has no tracks")

    track = _select_srt2images_track(tracks, run_name=run_dir.name, cues_count=len(cues))
    track_name = str(track.get("name") or "")

    _retime_segments(track, cues, fps)
    content["tracks"] = tracks

    # Keep a copy of the updated cues inside the draft for debugging/recovery.
    try:
        (draft_dir / "image_cues.json").write_bytes(cues_path.read_bytes())
    except Exception:
        pass

    _atomic_write_json(content_path, content)
    sync_draft_info_with_content(draft_dir)

    print(f"✅ retimed images: {draft_dir.name}")
    print(f"  - track: {track_name}")
    print(f"  - cues: {len(cues)} (fps={fps})")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Normalize srt2images track segment intervals inside a CapCut draft to a target range.

Goal:
  - Keep images/materials intact (no regeneration, no external LLM).
  - Ensure each srt2images segment duration is within [min_sec, max_sec].
  - Optionally rebuild the whole track (adds segments by cycling existing ones) when many segments exceed max.

This edits BOTH:
  - draft_content.json
  - draft_info.json

Usage:
  PYTHONPATH=".:packages" python3 -m video_pipeline.tools.normalize_srt2images_intervals \\
    --draft "$HOME/Movies/CapCut/User Data/Projects/com.lveditor.draft/★CH04-018-..." \\
    --min-sec 15 --max-sec 25 --target-sec 20 --mode auto
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import random
import shutil
import sys
import time
import uuid
from pathlib import Path


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _find_srt2images_track(data: dict) -> dict | None:
    tracks = data.get("tracks") or data.get("script", {}).get("tracks") or []
    if isinstance(tracks, dict):
        tracks = list(tracks.values())
    if not isinstance(tracks, list):
        return None
    for track in tracks:
        name = (track.get("name") or track.get("id") or "").lower()
        if name.startswith("srt2images_") and track.get("type") == "video":
            return track
    return None


def _duration_frames(duration_us: int, fps: int) -> int:
    return int(round(duration_us * fps / 1_000_000))


def _time_us_from_frames(frames: int, fps: int) -> int:
    return int(round(frames * 1_000_000 / fps))


def _track_end_frames(segments: list[dict], fps: int) -> int:
    end_frames = 0
    for seg in segments:
        tt = seg.get("target_timerange") or {}
        start_us = tt.get("start")
        dur_us = tt.get("duration")
        if not isinstance(start_us, int) or not isinstance(dur_us, int):
            continue
        start_frames = _duration_frames(start_us, fps)
        dur_frames = _duration_frames(dur_us, fps)
        end_frames = max(end_frames, start_frames + dur_frames)
    return end_frames


def _segment_duration_frames(seg: dict, fps: int) -> int | None:
    tt = seg.get("target_timerange") or {}
    dur_us = tt.get("duration")
    if not isinstance(dur_us, int):
        return None
    return _duration_frames(dur_us, fps)


def _count_out_of_range(segments: list[dict], fps: int, min_frames: int, max_frames: int) -> tuple[int, int]:
    lt = 0
    gt = 0
    for seg in segments:
        d = _segment_duration_frames(seg, fps)
        if d is None:
            continue
        if d < min_frames:
            lt += 1
        if d > max_frames:
            gt += 1
    return lt, gt


def _set_timeranges(seg: dict, *, start_frames: int, dur_frames: int, fps: int) -> None:
    start_us = _time_us_from_frames(start_frames, fps)
    dur_us = _time_us_from_frames(dur_frames, fps)

    seg.setdefault("target_timerange", {})
    seg.setdefault("source_timerange", {})
    seg.setdefault("render_timerange", {})

    seg["target_timerange"]["start"] = start_us
    seg["target_timerange"]["duration"] = dur_us

    seg["source_timerange"]["start"] = 0
    seg["source_timerange"]["duration"] = dur_us

    seg["render_timerange"]["start"] = 0
    seg["render_timerange"]["duration"] = dur_us


def _choose_segment_count(total_frames: int, *, min_frames: int, max_frames: int, target_frames: int, prefer_n: int | None) -> int:
    min_n = int(math.ceil(total_frames / max_frames))
    max_n = int(math.floor(total_frames / min_frames))
    if min_n > max_n:
        raise ValueError(f"Cannot satisfy constraints: total={total_frames} frames, range=[{min_frames},{max_frames}]")

    if prefer_n is not None and min_n <= prefer_n <= max_n:
        return prefer_n

    n = int(round(total_frames / target_frames)) if target_frames > 0 else min_n
    return min(max(n, min_n), max_n)


def _build_durations_frames(
    *,
    total_frames: int,
    n: int,
    min_frames: int,
    max_frames: int,
    target_frames: int,
    seed: int,
) -> list[int]:
    rng = random.Random(seed)
    remaining = total_frames
    durations: list[int] = []
    for idx in range(n):
        left = n - idx
        if left == 1:
            durations.append(remaining)
            break

        min_allowed = max(min_frames, remaining - max_frames * (left - 1))
        max_allowed = min(max_frames, remaining - min_frames * (left - 1))
        if min_allowed > max_allowed:
            raise ValueError("No feasible duration window while building durations")

        # Triangular distribution keeps it varied without looking robotic.
        mode = max(min(target_frames, max_allowed), min_allowed)
        dur = int(rng.triangular(min_allowed, max_allowed, mode))
        dur = max(min(dur, max_allowed), min_allowed)

        durations.append(dur)
        remaining -= dur

    if sum(durations) != total_frames:
        raise ValueError("Duration sum mismatch after build")
    if any(d < min_frames or d > max_frames for d in durations):
        raise ValueError("Built durations out of range")
    return durations


def _retime_global(
    *,
    ctrack: dict,
    itrack: dict,
    fps: int,
    min_frames: int,
    max_frames: int,
    target_frames: int,
    prefer_n: int | None,
    seed: int,
) -> dict:
    csegs0 = list(ctrack.get("segments") or [])
    isegs0 = list(itrack.get("segments") or [])
    if not csegs0 or not isegs0:
        raise ValueError("srt2images track has no segments")
    if len(csegs0) != len(isegs0):
        raise ValueError(f"content/info segment count mismatch: {len(csegs0)} vs {len(isegs0)}")

    total_frames = _track_end_frames(isegs0, fps)
    n = _choose_segment_count(
        total_frames,
        min_frames=min_frames,
        max_frames=max_frames,
        target_frames=target_frames,
        prefer_n=prefer_n,
    )
    durations = _build_durations_frames(
        total_frames=total_frames,
        n=n,
        min_frames=min_frames,
        max_frames=max_frames,
        target_frames=target_frames,
        seed=seed,
    )

    # Cycle existing segments to avoid adding new materials.
    csegs_new: list[dict] = []
    isegs_new: list[dict] = []
    start_frames = 0
    orig_n = len(csegs0)
    for i, dur in enumerate(durations):
        base_idx = i % orig_n
        cseg = copy.deepcopy(csegs0[base_idx])
        iseg = copy.deepcopy(isegs0[base_idx])

        if i >= orig_n:
            new_id = uuid.uuid4().hex
            cseg["id"] = new_id
            iseg["id"] = new_id

        _set_timeranges(cseg, start_frames=start_frames, dur_frames=dur, fps=fps)
        _set_timeranges(iseg, start_frames=start_frames, dur_frames=dur, fps=fps)

        csegs_new.append(cseg)
        isegs_new.append(iseg)
        start_frames += dur

    ctrack["segments"] = csegs_new
    itrack["segments"] = isegs_new
    return {"mode": "global", "segments": n, "total_frames": total_frames}


def _retime_redistribute(
    *,
    ctrack: dict,
    itrack: dict,
    fps: int,
    min_frames: int,
    max_frames: int,
) -> dict:
    csegs = list(ctrack.get("segments") or [])
    isegs = list(itrack.get("segments") or [])
    if not csegs or not isegs:
        raise ValueError("srt2images track has no segments")
    if len(csegs) != len(isegs):
        raise ValueError(f"content/info segment count mismatch: {len(csegs)} vs {len(isegs)}")

    durations = []
    for seg in isegs:
        d = _segment_duration_frames(seg, fps)
        if d is None:
            raise ValueError("Missing duration in segment target_timerange")
        durations.append(d)

    total_frames = sum(durations)

    excess_total = 0
    for i, d in enumerate(durations):
        if d > max_frames:
            excess = d - max_frames
            durations[i] = max_frames
            excess_total += excess

    if excess_total == 0:
        # Just normalize start times and timeranges for consistency.
        pass
    else:
        # Distribute excess forward then backward to segments with headroom.
        remaining = excess_total
        for i in range(len(durations)):
            if remaining <= 0:
                break
            headroom = max_frames - durations[i]
            if headroom <= 0:
                continue
            add = min(headroom, remaining)
            durations[i] += add
            remaining -= add

        if remaining > 0:
            raise ValueError("Unable to redistribute excess without violating max")

    if any(d < min_frames or d > max_frames for d in durations):
        raise ValueError("Redistribute produced out-of-range durations")
    if sum(durations) != total_frames:
        raise ValueError("Redistribute duration sum mismatch")

    start_frames = 0
    for i, dur in enumerate(durations):
        _set_timeranges(csegs[i], start_frames=start_frames, dur_frames=dur, fps=fps)
        _set_timeranges(isegs[i], start_frames=start_frames, dur_frames=dur, fps=fps)
        start_frames += dur

    ctrack["segments"] = csegs
    itrack["segments"] = isegs
    return {"mode": "redistribute", "segments": len(durations), "total_frames": total_frames}


def _compute_stats(segments: list[dict], fps: int) -> dict:
    durs = []
    for seg in segments:
        d = _segment_duration_frames(seg, fps)
        if d is not None:
            durs.append(d)
    if not durs:
        return {"segments": 0}
    sec = [d / fps for d in durs]
    return {
        "segments": len(durs),
        "min_sec": min(sec),
        "max_sec": max(sec),
        "avg_sec": sum(sec) / len(sec),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Normalize srt2images segment intervals inside a CapCut draft")
    ap.add_argument("--draft", action="append", required=True, help="CapCut draft dir (repeatable)")
    ap.add_argument("--min-sec", type=float, default=15.0)
    ap.add_argument("--max-sec", type=float, default=25.0)
    ap.add_argument("--target-sec", type=float, default=20.0)
    ap.add_argument("--mode", choices=["auto", "global", "redistribute"], default="auto")
    ap.add_argument("--seed", type=int, default=0, help="Deterministic seed (0 => per-draft derived)")
    ap.add_argument("--dry-run", action="store_true", help="Only print stats; do not write")
    args = ap.parse_args()

    min_sec = float(args.min_sec)
    max_sec = float(args.max_sec)
    if min_sec <= 0 or max_sec <= 0 or min_sec > max_sec:
        print("❌ invalid min/max seconds")
        return 2

    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

    for draft_str in args.draft:
        draft_dir = Path(draft_str).expanduser()
        if not draft_dir.exists():
            print(f"❌ draft not found: {draft_dir}")
            return 2

        content_path = draft_dir / "draft_content.json"
        info_path = draft_dir / "draft_info.json"
        if not content_path.exists() or not info_path.exists():
            print(f"❌ draft_content.json or draft_info.json not found: {draft_dir}")
            return 2

        content = _load_json(content_path)
        info = _load_json(info_path)
        fps = int(content.get("fps") or 30)
        ctrack = _find_srt2images_track(content)
        itrack = _find_srt2images_track(info)
        if not ctrack or not itrack:
            print(f"❌ srt2images track not found: {draft_dir}")
            return 2

        csegs = list(ctrack.get("segments") or [])
        isegs = list(itrack.get("segments") or [])
        if len(csegs) != len(isegs):
            print(f"❌ segment count mismatch: content={len(csegs)} info={len(isegs)} ({draft_dir})")
            return 2

        min_frames = int(math.ceil(min_sec * fps))
        max_frames = int(math.floor(max_sec * fps))
        target_frames = int(round(float(args.target_sec) * fps))
        if min_frames <= 0 or max_frames <= 0 or min_frames > max_frames:
            print("❌ invalid min/max in frames")
            return 2

        before = _compute_stats(isegs, fps)
        lt, gt = _count_out_of_range(isegs, fps, min_frames, max_frames)

        mode = args.mode
        if mode == "auto":
            # If many segments exceed max, do global rebuild (adds segments by cycling existing ones).
            if gt >= max(3, int(0.2 * max(1, len(isegs)))):
                mode = "global"
            else:
                mode = "redistribute"

        if args.seed != 0:
            seed = args.seed
        else:
            seed = abs(hash(str(draft_dir))) % (2**31)

        if args.dry_run:
            print(f"[DRY] {draft_dir.name} fps={fps} segs={before.get('segments')} lt={lt} gt={gt} stats={before}")
            continue

        shutil.copy2(content_path, str(content_path) + f".bak_imgretime_{ts}")
        shutil.copy2(info_path, str(info_path) + f".bak_imgretime_{ts}")

        if mode == "global":
            result = _retime_global(
                ctrack=ctrack,
                itrack=itrack,
                fps=fps,
                min_frames=min_frames,
                max_frames=max_frames,
                target_frames=target_frames,
                prefer_n=None,
                seed=seed,
            )
        else:
            result = _retime_redistribute(
                ctrack=ctrack,
                itrack=itrack,
                fps=fps,
                min_frames=min_frames,
                max_frames=max_frames,
            )

        after = _compute_stats(list(itrack.get("segments") or []), fps)
        lt2, gt2 = _count_out_of_range(list(itrack.get("segments") or []), fps, min_frames, max_frames)
        if lt2 or gt2:
            print(f"❌ post-check failed: lt={lt2} gt={gt2} draft={draft_dir}")
            return 2

        _save_json(content_path, content)
        _save_json(info_path, info)

        print(f"✅ {draft_dir.name}: {result['mode']} -> {after['segments']} segs (min={after['min_sec']:.2f}s max={after['max_sec']:.2f}s avg={after['avg_sec']:.2f}s)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


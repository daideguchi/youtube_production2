#!/usr/bin/env python3
"""
Align an existing srt2images run_dir to the canonical audio_tts_v2 final SRT/WAV.

Why:
  - If image_cues.json was generated from a stale/cached SRT, inserting the final audio/subtitles
    later will cause obvious timeline drift.
  - This tool retimes existing cues to the final SRT *without calling any LLM* by aligning each
    cue's combined text to the final SRT segment sequence.

What it does:
  1) Resolves episode_id from run_dir name (e.g., CH06-002_capcut_v1 -> CH06-002).
  2) Loads workspaces/audio/final/<CH>/<NNN>/<CH>-<NNN>.srt/.wav as SoT.
  3) Copies the final SRT into run_dir as <CH>-<NNN>.srt (backing up any differing prior copy).
  4) Retimes each cue (start/end/frame) by mapping cue.text -> final SRT segment range.
  5) Overwrites image_cues.json (backup created) and writes timeline_manifest.json (strict validation).

Usage:
  python3 tools/align_run_dir_to_tts_final.py --run workspaces/video/runs/CH06-002_capcut_v1
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, List, Optional, Tuple

import sys

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

from factory_common.paths import repo_root, video_pkg_root  # noqa: E402

PROJECT_ROOT = video_pkg_root()
REPO_ROOT = repo_root()

from factory_common.timeline_manifest import (
    EpisodeId,
    build_timeline_manifest,
    parse_episode_id,
    resolve_final_audio_srt,
    write_timeline_manifest,
)


_TC_RE = re.compile(
    r"^(?P<sh>\d{2}):(?P<sm>\d{2}):(?P<ss>\d{2})(?P<sms>[\.,]\d{1,3})?\s+-->\s+"
    r"(?P<eh>\d{2}):(?P<em>\d{2}):(?P<es>\d{2})(?P<ems>[\.,]\d{1,3})?\s*$"
)


def _now_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_text(text: str) -> str:
    t = (text or "").lower()
    t = re.sub(r"\s+", "", t)
    # drop common punctuation/symbol noise for matching
    t = re.sub(r"[「」『』（）()\[\]【】<>＜＞]", "", t)
    t = re.sub(r"[。、】【、，,.!！?？:：;；・…—–\-]", "", t)
    return t


def parse_srt_segments(path: Path) -> List[dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    segs: List[dict[str, Any]] = []
    i = 0
    n = len(lines)

    def _to_sec(h: int, m: int, s: int, ms_part: Optional[str]) -> float:
        ms = 0
        if ms_part:
            frac = (ms_part[1:] + "000")[:3]
            ms = int(frac)
        return h * 3600 + m * 60 + s + ms / 1000.0

    while i < n:
        while i < n and not _TC_RE.match(lines[i].strip()):
            i += 1
        if i >= n:
            break
        m = _TC_RE.match(lines[i].strip())
        i += 1
        if not m:
            continue
        sh, sm, ss, sms = int(m.group("sh")), int(m.group("sm")), int(m.group("ss")), m.group("sms")
        eh, em, es, ems = int(m.group("eh")), int(m.group("em")), int(m.group("es")), m.group("ems")
        start = _to_sec(sh, sm, ss, sms)
        end = _to_sec(eh, em, es, ems)
        txt_lines: List[str] = []
        while i < n and lines[i].strip() != "":
            txt_lines.append(lines[i].strip())
            i += 1
        while i < n and lines[i].strip() == "":
            i += 1
        block = " ".join(txt_lines).strip()
        if block:
            segs.append({"start": start, "end": end, "text": block, "norm": _normalize_text(block)})
    segs.sort(key=lambda x: x["start"])
    return segs


def _best_range_for_cue(
    cue_norm: str,
    segs: List[dict[str, Any]],
    start_hint: int,
    *,
    max_start_lookahead: int = 6,
    max_span: int = 60,
) -> Tuple[int, int, float]:
    """
    Find (start_idx, end_idx, score) mapping cue_norm to a contiguous segment range.
    Greedy windowed search from start_hint.
    """
    if not cue_norm:
        # Degenerate: map to at least one segment.
        s = min(max(start_hint, 0), len(segs) - 1)
        return s, s, 0.0

    best: Tuple[int, int, float] = (start_hint, start_hint, -1.0)
    n = len(segs)
    for s in range(start_hint, min(n, start_hint + max_start_lookahead)):
        buf = ""
        # incremental concat for speed
        for e in range(s, min(n, s + max_span)):
            buf += segs[e]["norm"]
            if not buf:
                continue
            # Similarity + length proximity (avoid overly wide ranges)
            ratio = SequenceMatcher(None, cue_norm, buf).ratio()
            len_pen = abs(len(buf) - len(cue_norm)) / max(1, len(cue_norm))
            score = ratio - (len_pen * 0.15)
            if score > best[2]:
                best = (s, e, score)
            # Early exit when we're already very good and length is close
            if ratio >= 0.98 and len_pen <= 0.05:
                return best
            # Stop when buf is way longer than cue (further e will only grow)
            if len(buf) > len(cue_norm) * 1.8 and ratio < 0.9:
                break
    return best


def retime_cues_to_final_srt(
    cues_payload: dict[str, Any],
    final_segments: List[dict[str, Any]],
    *,
    min_score: float = 0.72,
) -> dict[str, Any]:
    cues = list(cues_payload.get("cues") or [])
    if not cues:
        raise RuntimeError("image_cues.json has no cues")
    fps = int(cues_payload.get("fps") or 30)

    aligned: List[Tuple[int, int, float]] = []
    pos = 0
    for i, cue in enumerate(cues):
        cue_text = cue.get("text") or cue.get("summary") or ""
        cue_norm = _normalize_text(cue_text)
        if pos >= len(final_segments):
            raise RuntimeError(f"Alignment ran past final SRT segments at cue#{i+1}")
        s, e, score = _best_range_for_cue(cue_norm, final_segments, pos)
        if score < min_score:
            raise RuntimeError(f"Low alignment score for cue#{i+1}: score={score:.3f} start_hint={pos} range=({s},{e})")
        aligned.append((s, e, score))
        pos = e + 1

    # If we didn't consume all segments, extend the last cue to include remaining.
    if aligned and aligned[-1][1] < len(final_segments) - 1:
        s, _e, sc = aligned[-1]
        aligned[-1] = (s, len(final_segments) - 1, sc)

    # Apply timing
    for cue, (s, e, _score) in zip(cues, aligned):
        start_sec = float(final_segments[s]["start"])
        end_sec = float(final_segments[e]["end"])
        cue["start_sec"] = round(start_sec, 3)
        cue["end_sec"] = round(end_sec, 3)

    # Enforce continuity (no gaps, no overlaps) like cue_maker does
    for i in range(len(cues) - 1):
        next_start = float(cues[i + 1]["start_sec"])
        cues[i]["end_sec"] = round(next_start, 3)
    # Recompute durations/frames
    for cue in cues:
        start_sec = float(cue.get("start_sec", 0.0))
        end_sec = float(cue.get("end_sec", start_sec))
        if end_sec < start_sec:
            end_sec = start_sec
        cue["duration_sec"] = round(end_sec - start_sec, 3)
        cue["start_frame"] = int(round(start_sec * fps))
        cue["end_frame"] = int(round(end_sec * fps))
        cue["duration_frames"] = max(1, cue["end_frame"] - cue["start_frame"])

    out = dict(cues_payload)
    out["cues"] = cues
    return out


def _is_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def retime_cues_by_scale(
    cues_payload: dict[str, Any],
    final_segments: List[dict[str, Any]],
) -> dict[str, Any]:
    """
    Fallback retiming when cue.text no longer matches the final SRT sufficiently.

    Strategy:
      - Keep the relative timing of the existing cues, but scale them so that the
        last cue ends exactly at the final SRT end time.
      - Enforce continuity (no gaps/overlaps) and recompute frames/durations.

    This does NOT attempt semantic/text alignment; it is deterministic and LLM-free.
    """
    cues = list(cues_payload.get("cues") or [])
    if not cues:
        raise RuntimeError("image_cues.json has no cues")
    if not final_segments:
        raise RuntimeError("final SRT has no segments")
    fps = int(cues_payload.get("fps") or 30)

    new_end = float(final_segments[-1]["end"])

    old_end = 0.0
    for cue in cues:
        end_sec = cue.get("end_sec")
        if _is_num(end_sec):
            old_end = max(old_end, float(end_sec))
            continue
        end_frame = cue.get("end_frame")
        if _is_num(end_frame):
            old_end = max(old_end, float(end_frame) / max(1.0, float(fps)))

    if old_end <= 0.0:
        raise RuntimeError("Could not determine cues_end_sec for scale retiming")

    ratio = new_end / old_end

    # Scale start/end while preserving order.
    cues.sort(key=lambda c: int(c.get("index") or 0))
    for cue in cues:
        start_sec = cue.get("start_sec")
        end_sec = cue.get("end_sec")
        if not _is_num(start_sec):
            sf = cue.get("start_frame")
            start_sec = float(sf) / max(1.0, float(fps)) if _is_num(sf) else 0.0
        if not _is_num(end_sec):
            ef = cue.get("end_frame")
            end_sec = float(ef) / max(1.0, float(fps)) if _is_num(ef) else float(start_sec)
        start_sec = float(start_sec) * ratio
        end_sec = float(end_sec) * ratio
        cue["start_sec"] = round(max(0.0, start_sec), 3)
        cue["end_sec"] = round(max(0.0, end_sec), 3)

    # Enforce continuity (no gaps/overlaps) and exact final end.
    if cues:
        cues[0]["start_sec"] = 0.0
    for i in range(len(cues) - 1):
        next_start = float(cues[i + 1]["start_sec"])
        cues[i]["end_sec"] = round(next_start, 3)
    cues[-1]["end_sec"] = round(new_end, 3)

    # Recompute durations/frames.
    for cue in cues:
        start_sec = float(cue.get("start_sec", 0.0))
        end_sec = float(cue.get("end_sec", start_sec))
        if end_sec < start_sec:
            end_sec = start_sec
        cue["duration_sec"] = round(end_sec - start_sec, 3)
        cue["start_frame"] = int(round(start_sec * fps))
        cue["end_frame"] = int(round(end_sec * fps))
        cue["duration_frames"] = max(1, cue["end_frame"] - cue["start_frame"])

    out = dict(cues_payload)
    out["cues"] = cues
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="run_dir containing image_cues.json and images/")
    ap.add_argument("--min-score", type=float, default=0.72, help="minimum alignment score threshold (default: 0.72)")
    ap.add_argument(
        "--fallback-scale",
        action="store_true",
        help="If text alignment fails, retime cues by scaling to final SRT end time (deterministic; no LLM)",
    )
    args = ap.parse_args()

    run_dir = Path(args.run).expanduser().resolve()
    if not run_dir.exists():
        raise SystemExit(f"run_dir not found: {run_dir}")

    episode = parse_episode_id(run_dir.name) or parse_episode_id(str(run_dir))
    if not episode:
        raise SystemExit(f"Could not parse episode id from run_dir: {run_dir.name}")

    wav_path, srt_path = resolve_final_audio_srt(episode)
    if not wav_path.exists() or not srt_path.exists():
        raise SystemExit(f"Final artifacts missing for {episode.episode}")

    cues_path = run_dir / "image_cues.json"
    if not cues_path.exists():
        raise SystemExit(f"image_cues.json not found: {cues_path}")

    # Backup + sync SRT
    target_srt = run_dir / f"{episode.episode}.srt"
    if target_srt.exists():
        if target_srt.read_bytes() != srt_path.read_bytes():
            bak = run_dir / f"{target_srt.stem}.legacy.{_now_tag()}.srt"
            target_srt.replace(bak)
            target_srt.write_bytes(srt_path.read_bytes())
    else:
        target_srt.write_bytes(srt_path.read_bytes())

    # Retiming is computed first; only if successful do we rotate files on disk.
    cues_payload = _read_json(cues_path)
    final_segments = parse_srt_segments(target_srt)
    mode = "text"
    try:
        new_cues = retime_cues_to_final_srt(cues_payload, final_segments, min_score=float(args.min_score))
    except Exception:
        if not args.fallback_scale:
            raise
        mode = "scale"
        new_cues = retime_cues_by_scale(cues_payload, final_segments)

    tag = _now_tag()
    legacy_cues = run_dir / f"image_cues.legacy.{tag}.json"
    tmp_cues = run_dir / f"image_cues.tmp.{tag}.json"
    _write_json(tmp_cues, new_cues)
    cues_path.replace(legacy_cues)
    tmp_cues.replace(cues_path)

    # Strict manifest validation (also checks image existence)
    manifest = build_timeline_manifest(
        run_dir=run_dir,
        episode=episode,
        audio_wav=wav_path,
        audio_srt=target_srt,
        image_cues_path=cues_path,
        belt_config_path=(run_dir / "belt_config.json") if (run_dir / "belt_config.json").exists() else None,
        notes=f"align_run_dir_to_tts_final (retime cues via {mode}; no LLM)",
        validate=True,
    )
    write_timeline_manifest(run_dir, manifest)

    print(f"✅ aligned: {run_dir.name}")
    print(f"  - mode: {mode}")
    print(f"  - SRT: {target_srt.name}")
    print(f"  - cues: image_cues.json (backup: {legacy_cues.name})")
    print(f"  - manifest: {run_dir / 'timeline_manifest.json'}")


if __name__ == "__main__":
    main()

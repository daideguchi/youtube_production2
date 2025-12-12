#!/usr/bin/env python3
"""
Inject CH06 CapCut template layers (BGM multi-seg + dreamy confetti effect) into existing drafts.

What this does (per draft):
  - Adds BGM multi-segment audio track based on CH06-テンプレ.
  - Adds 'ドリーミー紙吹雪' video effect track based on ★CH06-001 draft.
  - Leaves existing tracks (voiceover/subtitles/images) untouched.
  - Backs up draft_content.json and draft_info.json before writing.

Usage:
  python3 tools/inject_ch06_template_layers.py \
    --draft-root "$HOME/Movies/CapCut/User Data/Projects/com.lveditor.draft" \
    --template "CH06-テンプレ" \
    --effect-source "★CH06-001-【禁断の福音書】トマスによる福音書【都市伝説のダーク図書館】" \
    --apply

Dry-run (no write):
  python3 tools/inject_ch06_template_layers.py --draft-root ... --template CH06-テンプレ --effect-source ... 
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple


SEC = 1_000_000  # CapCut uses microseconds


def _uuid_upper() -> str:
    return str(uuid.uuid4()).upper()


def _uuid_lower() -> str:
    return str(uuid.uuid4())


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def backup(path: Path) -> Path:
    bak = path.with_suffix(path.suffix + ".bak_ch06_inject")
    shutil.copy2(path, bak)
    return bak


def find_bgm_track(template_data: Dict[str, Any]) -> Dict[str, Any]:
    audio_tracks = [t for t in template_data.get("tracks", []) if t.get("type") == "audio"]
    if not audio_tracks:
        raise RuntimeError("No audio tracks in template")
    # BGM multi track = most segments
    return sorted(audio_tracks, key=lambda t: len(t.get("segments") or []), reverse=True)[0]


def collect_bgm_audios(template_data: Dict[str, Any], bgm_track: Dict[str, Any]) -> List[Dict[str, Any]]:
    ids = {s.get("material_id") for s in (bgm_track.get("segments") or [])}
    audios = template_data.get("materials", {}).get("audios") or []
    return [a for a in audios if a.get("id") in ids]


def build_bgm_injection(
    template_data: Dict[str, Any],
    target_duration: int,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    bgm_track_tpl = find_bgm_track(template_data)
    bgm_audios_tpl = collect_bgm_audios(template_data, bgm_track_tpl)
    if not bgm_audios_tpl:
        raise RuntimeError("Template BGM audios not found")

    seg_tpl = bgm_track_tpl["segments"][0]
    audio_tpl = bgm_audios_tpl[0]
    seg_len = int(audio_tpl.get("duration") or seg_tpl.get("target_timerange", {}).get("duration") or 0)
    if seg_len <= 0:
        raise RuntimeError("Invalid BGM segment length")

    new_segments: List[Dict[str, Any]] = []
    new_audios: List[Dict[str, Any]] = []

    start = 0
    while start < target_duration:
        dur = min(seg_len, target_duration - start)

        audio_obj = copy.deepcopy(audio_tpl)
        audio_id = _uuid_upper()
        audio_obj["id"] = audio_id
        audio_obj["local_material_id"] = _uuid_lower()
        audio_obj["music_id"] = _uuid_lower()
        # keep full file duration for metadata; last segment may be shorter
        audio_obj["duration"] = seg_len
        new_audios.append(audio_obj)

        seg_obj = copy.deepcopy(seg_tpl)
        seg_obj["id"] = _uuid_upper()
        seg_obj["material_id"] = audio_id
        seg_obj["target_timerange"] = {"start": start, "duration": dur}
        seg_obj["source_timerange"] = {"start": 0, "duration": dur}
        new_segments.append(seg_obj)

        start += dur

    track_obj = copy.deepcopy(bgm_track_tpl)
    track_obj["id"] = _uuid_upper()
    track_obj["segments"] = new_segments
    # Keep name/attributes as template (unnamed audio track)
    return track_obj, new_audios


def build_effect_injection(
    effect_source_data: Dict[str, Any],
    target_duration: int,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    effect_track_tpl = next((t for t in effect_source_data.get("tracks", []) if t.get("type") == "effect"), None)
    if not effect_track_tpl:
        raise RuntimeError("Effect source draft has no effect track")
    effect_material_tpl = next(
        (m for m in effect_source_data.get("materials", {}).get("video_effects", []) or [] if m.get("name") == "ドリーミー紙吹雪"),
        None,
    )
    if not effect_material_tpl:
        raise RuntimeError("Effect material 'ドリーミー紙吹雪' not found in source")

    material_obj = copy.deepcopy(effect_material_tpl)
    material_id = _uuid_upper()
    material_obj["id"] = material_id

    track_obj = copy.deepcopy(effect_track_tpl)
    track_obj["id"] = _uuid_upper()
    seg = copy.deepcopy(track_obj["segments"][0])
    seg["id"] = _uuid_upper()
    seg["material_id"] = material_id
    seg["target_timerange"] = {"start": 0, "duration": int(target_duration)}
    seg["source_timerange"] = None
    track_obj["segments"] = [seg]
    return track_obj, material_obj


def has_bgm_multi_track(draft_data: Dict[str, Any]) -> bool:
    for t in draft_data.get("tracks", []):
        if t.get("type") == "audio" and len(t.get("segments") or []) >= 3:
            # Heuristic: multi segments == bgm multi
            return True
    return False


def has_confetti_effect(draft_data: Dict[str, Any]) -> bool:
    if any(t.get("type") == "effect" for t in draft_data.get("tracks", [])):
        return True
    return False


def inject_into_draft(
    draft_dir: Path,
    template_data: Dict[str, Any],
    effect_source_data: Dict[str, Any],
    apply: bool,
) -> Dict[str, Any]:
    content_path = draft_dir / "draft_content.json"
    info_path = draft_dir / "draft_info.json"
    data = load_json(content_path)
    duration = int(data.get("duration") or 0)
    if duration <= 0:
        raise RuntimeError(f"Invalid duration in {draft_dir.name}")

    actions: List[str] = []

    if not has_bgm_multi_track(data):
        bgm_track, bgm_audios = build_bgm_injection(template_data, duration)
        data.setdefault("tracks", []).insert(1, bgm_track)  # after base video track
        data.setdefault("materials", {}).setdefault("audios", []).extend(bgm_audios)
        actions.append(f"add_bgm_multi({len(bgm_track['segments'])} segs)")

    if not has_confetti_effect(data):
        eff_track, eff_material = build_effect_injection(effect_source_data, duration)
        data.setdefault("tracks", []).append(eff_track)
        data.setdefault("materials", {}).setdefault("video_effects", []).append(eff_material)
        actions.append("add_confetti_effect")

    if apply and actions:
        backup(content_path)
        content_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        if info_path.exists():
            try:
                info = load_json(info_path)
                info["tracks"] = data.get("tracks", [])
                info["materials"] = data.get("materials", {})
                info["duration"] = duration
                backup(info_path)
                info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                # best-effort; never block content write
                pass

    return {"draft": draft_dir.name, "actions": actions, "duration_sec": duration / SEC}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--draft-root", required=True, help="CapCut draft root directory")
    ap.add_argument("--template", required=True, help="CH06 template dir name under draft-root")
    ap.add_argument("--effect-source", required=True, help="Draft dir name that has dreamy confetti effect")
    ap.add_argument("--apply", action="store_true", help="Actually write changes (default: dry-run)")
    ap.add_argument("--pattern", default=r"^★CH06-(\d{3})-", help="Regex to select drafts")
    args = ap.parse_args()

    draft_root = Path(args.draft_root).expanduser()
    tpl_dir = draft_root / args.template
    eff_dir = draft_root / args.effect_source
    if not tpl_dir.exists():
        raise SystemExit(f"Template not found: {tpl_dir}")
    if not eff_dir.exists():
        raise SystemExit(f"Effect source not found: {eff_dir}")

    template_data = load_json(tpl_dir / "draft_content.json")
    effect_source_data = load_json(eff_dir / "draft_content.json")

    pat = re.compile(args.pattern)
    targets = []
    for d in draft_root.iterdir():
        if not d.is_dir():
            continue
        m = pat.match(d.name)
        if not m:
            continue
        vid = int(m.group(1))
        if 1 <= vid <= 30:
            targets.append((vid, d))
    targets.sort()

    reports = []
    for vid, d in targets:
        rep = inject_into_draft(d, template_data, effect_source_data, apply=args.apply)
        reports.append((vid, rep))

    for vid, rep in reports:
        acts = ", ".join(rep["actions"]) if rep["actions"] else "noop"
        print(f"CH06-{vid:03d}: {acts} (dur={rep['duration_sec']:.1f}s)")

    if not args.apply:
        print("Dry-run only. Re-run with --apply to write.")


if __name__ == "__main__":
    main()

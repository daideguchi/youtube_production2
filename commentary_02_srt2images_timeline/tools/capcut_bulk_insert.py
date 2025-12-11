#!/usr/bin/env python3
import argparse
import json
import sys
import re
from pathlib import Path
import copy
import inspect
import shutil
import time
import random

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# CapCut API path
# Ensure pyJianYingDraft is importable in local environments
_CANDIDATE_API_PATHS = [
    Path("/Users/dd/capcut_api"),
    Path(__file__).resolve().parents[2] / "50_tools" / "50_1_capcut_api",
]

for _candidate in _CANDIDATE_API_PATHS:
    if _candidate.exists():
        path_str = str(_candidate)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)

import pyJianYingDraft as draft
from pyJianYingDraft import Draft_folder, Track_type, Video_material, Video_segment, Text_segment, Clip_settings, Timerange, SEC, KeyframeProperty
from pyJianYingDraft import Text_style, Text_background, Text_border
from typing import Optional
import json as _json2
import os
import logging
import traceback
from config.channel_resolver import ChannelPresetResolver, infer_channel_id_from_path
from src.config.style_resolver import StyleResolver
from src.adapters.capcut.style_mapper import CapCutStyleAdapter

# Channel-specific post processors (per-channel hooks)
CHANNEL_HOOKS = {}

# CH02 hook implementation
def _apply_ch02_overrides(draft_dir: Path, belt_text: Optional[str] = None):
    """
    Apply CH02-specific overrides:
    - Belt text update (content/info both)
    - Keep existing BGM/Effect; no extra injection to avoid cross-channel mixing
    """
    try:
        for fname in ("draft_content.json", "draft_info.json"):
            path = draft_dir / fname
            if not path.exists():
                continue
            data = json.loads(path.read_text())
            # belt_main text update if provided
            if belt_text:
                belt = next((t for t in data.get("tracks", []) if t.get("name") == "belt_main"), None)
                if belt and belt.get("segments"):
                    mid = belt["segments"][0].get("material_id")
                    mats = data.get("materials", {}).get("texts", [])
                    for m in mats:
                        if m.get("id") == mid:
                            if isinstance(m.get("content"), dict):
                                m["content"]["text"] = belt_text
                            else:
                                m["content"] = belt_text if fname.endswith("info.json") else {"text": belt_text}
                            m["base_content"] = belt_text
                            m["name"] = m.get("name") or "belt_main_text"
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception as exc:
        logger.warning(f"CH02 override failed: {exc}")

# register hook
CHANNEL_HOOKS["CH02"] = _apply_ch02_overrides

try:
    from fix_fade_transitions_correct import (
        add_crossfade_transitions as _auto_add_crossfade_transitions,
        backup_file as _auto_backup_file,
        load_json as _auto_load_fade_json,
        save_json as _auto_save_fade_json,
        sync_draft_info as _auto_sync_draft_info,
    )
except Exception:  # pragma: no cover - optional dependency
    _auto_add_crossfade_transitions = None
    _auto_backup_file = None
    _auto_load_fade_json = None
    _auto_save_fade_json = None
    _auto_sync_draft_info = None

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Determine pyJianYingDraft API expectations for Video_material
try:
    _VIDEO_MATERIAL_REQUIRES_TYPE = (
        inspect.signature(Video_material).parameters['material_type'].default is inspect._empty
    )
except Exception:
    _VIDEO_MATERIAL_REQUIRES_TYPE = False


# ========================================
# 🛡️ DEFENSE SYSTEM - Template Validation
# ========================================

def validate_template(draft_root: Path, template_name: str) -> tuple[bool, str]:
    """
    Validate template existence and draft_content.json presence.

    Returns:
        (is_valid, error_message)
    """
    template_path = draft_root / template_name

    if not template_path.exists():
        return False, f"❌ Template '{template_name}' does not exist at {template_path}"

    if not template_path.is_dir():
        return False, f"❌ Template '{template_name}' is not a directory"

    draft_content = template_path / "draft_content.json"
    draft_info = template_path / "draft_info.json"

    if not draft_content.exists():
        if draft_info.exists():
            return False, (
                f"❌ Template '{template_name}' lacks draft_content.json\n"
                f"   (has draft_info.json but pyJianYingDraft 0.2.3 requires draft_content.json)"
            )
        else:
            return False, f"❌ Template '{template_name}' is not a valid CapCut draft (no JSON files)"

    return True, ""


def list_valid_templates(draft_root: Path, prefix: str = "") -> list[tuple[str, float]]:
    """
    List all valid templates (with draft_content.json).

    Returns:
        List of (template_name, mtime) sorted by modification time (newest first)
    """
    if not draft_root.exists():
        return []

    valid_templates = []
    for entry in draft_root.iterdir():
        if not entry.is_dir():
            continue

        if prefix and not entry.name.startswith(prefix):
            continue

        draft_content = entry / "draft_content.json"
        if draft_content.exists():
            mtime = draft_content.stat().st_mtime
            valid_templates.append((entry.name, mtime))

    valid_templates.sort(key=lambda x: x[1], reverse=True)
    return valid_templates


def find_best_template(draft_root: Path, prefix: str) -> Optional[str]:
    """
    Find the newest valid template matching the prefix.

    Returns:
        Template name or None if not found
    """
    candidates = list_valid_templates(draft_root, prefix)
    if candidates:
        return candidates[0][0]  # Return newest
    return None


def pre_flight_check(args, logger: logging.Logger) -> list[str]:
    """
    Comprehensive pre-flight validation before execution.

    Returns:
        List of error messages (empty if all checks pass)
    """
    errors = []

    # 1. Template validation
    draft_root = Path(args.draft_root)
    template_name = args.template

    valid, msg = validate_template(draft_root, template_name)
    if not valid:
        errors.append(msg)

        # Suggest alternatives
        valid_templates = list_valid_templates(draft_root, args.preferred_template_prefix)
        if valid_templates:
            errors.append("\n💡 Available valid templates:")
            for name, mtime in valid_templates[:5]:
                from datetime import datetime
                dt = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M')
                errors.append(f"   • {name} (modified: {dt})")

    # 2. Input directory validation
    run_dir = Path(args.run)
    if not run_dir.exists():
        errors.append(f"❌ Run directory not found: {args.run}")
    else:
        image_cues = run_dir / "image_cues.json"
        if not image_cues.exists():
            errors.append(f"❌ image_cues.json not found in {args.run}")

        images_dir = run_dir / "images"
        if not images_dir.exists():
            errors.append(f"❌ images/ directory not found in {args.run}")

    # 3. SRT file validation (if specified)
    if args.srt_file:
        srt_path = Path(args.srt_file)
        if not srt_path.exists():
            errors.append(f"❌ SRT file not found: {args.srt_file}")

    # 4. Voice file validation (if specified)
    if hasattr(args, 'voice_file') and args.voice_file:
        voice_path = Path(args.voice_file)
        if not voice_path.exists():
            errors.append(f"❌ Voice file not found: {args.voice_file}")

    # 5. Write permission check
    if not os.access(args.draft_root, os.W_OK):
        errors.append(f"❌ No write permission to draft root: {args.draft_root}")

    # 6. Draft name conflict check
    new_draft_path = draft_root / args.new
    if new_draft_path.exists():
        logger.warning(f"⚠️  Draft '{args.new}' already exists and will be replaced")

    return errors


# ========================================
# Original functions
# ========================================

def load_cues(run_dir: Path):
    data = json.loads((run_dir / "image_cues.json").read_text(encoding="utf-8"))
    fps = int(data["fps"])
    size = data["size"]
    crossfade = float(data.get("crossfade", 0.0))
    cues = data["cues"]
    return fps, size, crossfade, cues


def parse_srt_file(srt_path: Path):
    """Parse SRT file and return list of subtitle entries."""
    if not srt_path.exists():
        return []
    
    content = srt_path.read_text(encoding='utf-8')
    # SRT pattern: index, time, text, blank line
    pattern = r'(\d+)\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n(.*?)(?=\n\n|\n\d+\n|\Z)'
    matches = re.findall(pattern, content, re.DOTALL)
    
    subtitles = []
    for match in matches:
        index, start_time, end_time, text = match
        
        # Convert time to microseconds
        def time_to_us(time_str):
            h, m, s_ms = time_str.split(':')
            s, ms = s_ms.split(',')
            return (int(h) * 3600 + int(m) * 60 + int(s)) * 1000000 + int(ms) * 1000
        
        subtitles.append({
            'index': int(index),
            'start_us': time_to_us(start_time),
            'end_us': time_to_us(end_time),
            'text': text.strip().replace('\n', ' ')
        })
    
    return subtitles


def make_absolute_schedule_us(cues, offset_us=0):
    """Use absolute timing from image_cues.json (start_sec/end_sec) instead of contiguous layout.
    Returns list of (start_us, dur_us) for each cue.

    Args:
        cues: Image cues from image_cues.json
        offset_us: Time offset in microseconds to add to all timestamps (for opening)
    """
    SEC_US = 1_000_000
    starts = []
    for c in cues:
        start_us = int(round(float(c.get("start_sec", 0.0)) * SEC_US)) + offset_us
        end_us = int(round(float(c.get("end_sec", 0.0)) * SEC_US)) + offset_us
        if end_us <= start_us:
            # Fallback to duration_sec if provided; else minimum 1 frame at 30fps
            dur_us = int(round(float(c.get("duration_sec", 1.0)) * SEC_US))
        else:
            dur_us = end_us - start_us
        starts.append((start_us, max(SEC // 60, dur_us)))  # minimum ~16ms to avoid zero-length
    return starts


def ensure_video_track(script: draft.Script_file, name: str, absolute_index: Optional[int] = None):
    """Ensure a top-most video track exists with predictable name.
    If the track exists, bump its absolute_index to an extremely high number
    to guarantee it renders above all template overlays.
    """
    HIGH = 999999 if absolute_index is None else absolute_index
    try:
        script.add_track(Track_type.video, name, absolute_index=HIGH)
    except Exception:
        # If it already exists, try to bump its index
        try:
            tr = script.tracks[name]
            # Some pyJianYingDraft versions expose .absolute_index, others .render_index
            if hasattr(tr, 'absolute_index'):
                tr.absolute_index = HIGH
            elif hasattr(tr, 'render_index'):
                tr.render_index = HIGH
        except Exception:
            pass


def _load_draft_track_indices(draft_dir: Path) -> list[int]:
    """Parse draft_info.json to collect existing tracks' absolute indices.
    Return a list of integer indices (may be empty on failure).
    """
    indices: list[int] = []
    try:
        info_path = draft_dir / 'draft_info.json'
        data = _json2.loads(info_path.read_text(encoding='utf-8'))
        tracks = data.get('tracks') or data.get('script', {}).get('tracks')
        it = []
        if isinstance(tracks, dict):
            it = tracks.values()
        elif isinstance(tracks, list):
            it = tracks
        for tr in it:
            ai = tr.get('absolute_index') or tr.get('render_index') or tr.get('z_index')
            if isinstance(ai, int):
                indices.append(ai)
    except Exception:
        pass
    return indices


def _compute_abs_index_for_rank(draft_dir: Path, rank_from_top: int = 3, default_high: int = 1_000_000) -> int:
    """Compute an absolute_index so that the new track appears at the given rank from top.
    We try to place it below the top (rank-1) tracks but above the next, using the
    existing absolute_index distribution. Fallback to default_high-(rank-1).
    """
    try:
        indices = sorted(set(_load_draft_track_indices(draft_dir)), reverse=True)
        if not indices:
            return default_high - (rank_from_top - 1)
        # If there are fewer tracks than rank, place relative to current max
        if len(indices) < rank_from_top:
            return indices[0] - (rank_from_top - 1)
        # Desired window: strictly between indices[rank-2] (second for rank=3) and indices[rank-1]
        upper = indices[rank_from_top - 2]  # rank-1 from top
        lower = indices[rank_from_top - 1]  # rank from top currently
        # Try between upper and lower
        if upper - lower >= 2:
            return lower + 1
        # Otherwise, nudge below upper if possible
        cand = upper - 1
        if cand > lower:
            return cand
        # As a last resort, place at lower (may tie) or default fallback
        return lower
    except Exception:
        return default_high - (rank_from_top - 1)


def _read_tracks_meta(draft_dir: Path):
    try:
        info_path = draft_dir / 'draft_info.json'
        data = _json2.loads(info_path.read_text(encoding='utf-8'))
        tracks = data.get('tracks') or data.get('script', {}).get('tracks')
        if isinstance(tracks, dict):
            items = []
            for k, v in tracks.items():
                it = dict(v)
                it['name'] = k
                items.append(it)
            return items
        elif isinstance(tracks, list):
            return tracks
    except Exception:
        return []
    return []


def _compute_audio_voice_index_below_bgm(draft_dir: Path, fallback: int = 10) -> int:
    tracks = _read_tracks_meta(draft_dir)
    audio_indices = []
    for tr in tracks:
        if tr.get('type') == 'audio':
            ai = tr.get('absolute_index') or tr.get('render_index') or tr.get('z_index')
            if isinstance(ai, int):
                audio_indices.append(ai)
    if not audio_indices:
        return fallback
    bgm_idx = max(audio_indices)
    return max(1, bgm_idx - 1)


def fix_image_track_render_timerange(draft_dir: Path) -> bool:
    """
    Fix render_timerange for image/video segments.

    pyJianYingDraft may not set render_timerange correctly for image materials,
    causing CapCut to not display images. This function sets render_timerange
    to match the material's actual duration.

    Returns:
        True if fix successful, False otherwise
    """
    try:
        draft_content_path = draft_dir / "draft_content.json"
        if not draft_content_path.exists():
            logger.warning("Cannot fix render_timerange: draft_content.json not found")
            return False

        with open(draft_content_path, 'r', encoding='utf-8') as f:
            data = _json2.load(f)

        # Backup
        backup_path = draft_dir / "draft_content.json.bak_render_fix"
        with open(backup_path, 'w', encoding='utf-8') as f:
            _json2.dump(data, f, ensure_ascii=False, indent=2)

        # Fix all video tracks
        tracks = data.get('tracks', [])
        fixed_count = 0

        for track in tracks:
            if track.get('type') != 'video':
                continue

            for seg in track.get('segments', []):
                target_timerange = seg.get('target_timerange') or {}
                source_timerange = seg.get('source_timerange') or {}

                target_duration = target_timerange.get('duration')
                if target_duration is None:
                    target_duration = source_timerange.get('duration')

                if target_duration is None:
                    continue

                seg['render_timerange'] = {
                    'start': source_timerange.get('start', 0),
                    'duration': target_duration
                }
                fixed_count += 1

        logger.info(f"   Fixed render_timerange for {fixed_count} video segments")

        # Save
        with open(draft_content_path, 'w', encoding='utf-8') as f:
            _json2.dump(data, f, ensure_ascii=False, indent=2)

        return True

    except Exception as e:
        logger.error(f"Failed to fix render_timerange: {e}")
        return False


def _merge_info_tracks_into_content(draft_dir: Path) -> None:
    """Ensure draft_content.json mirrors new tracks/materials saved via pyJianYingDraft."""
    content_path = draft_dir / "draft_content.json"
    info_path = draft_dir / "draft_info.json"
    if not content_path.exists() or not info_path.exists():
        return

    try:
        content_data = _json2.loads(content_path.read_text(encoding='utf-8'))
        info_data = _json2.loads(info_path.read_text(encoding='utf-8'))

        content_tracks = content_data.get('tracks', [])
        info_tracks = info_data.get('tracks', [])

        content_track_index = {track.get('id'): idx for idx, track in enumerate(content_tracks) if track.get('id')}
        for track in info_tracks:
            tid = track.get('id')
            if tid and tid in content_track_index:
                content_tracks[content_track_index[tid]] = track
            else:
                content_tracks.append(track)

        content_materials = content_data.setdefault('materials', {})
        info_materials = info_data.get('materials', {})
        for mat_type, info_list in info_materials.items():
            if not isinstance(info_list, list):
                continue
            content_list = content_materials.setdefault(mat_type, [])
            content_index = {item.get('id'): idx for idx, item in enumerate(content_list) if isinstance(item, dict) and item.get('id')}
            for item in info_list:
                if not isinstance(item, dict):
                    continue
                mid = item.get('id')
                if mid and mid in content_index:
                    content_list[content_index[mid]] = item
                else:
                    content_list.append(item)

        content_path.write_text(_json2.dumps(content_data, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as exc:
        logger.warning(f"Failed to merge draft_info into draft_content: {exc}")


def ensure_absolute_indices(draft_dir: Path) -> None:
    """
    CapCut may hide tracks if absolute_index is missing. Assign deterministic values.
    Priority: subtitles_text > srt2images_* > text > audio > video.
    """
    try:
        content_path = draft_dir / "draft_content.json"
        info_path = draft_dir / "draft_info.json"
        if not content_path.exists() or not info_path.exists():
            return

        def _assign(tracks):
            if not isinstance(tracks, list):
                return
            for idx, tr in enumerate(tracks):
                if not isinstance(tr, dict):
                    continue
                if tr.get("absolute_index") is not None:
                    continue
                name = tr.get("name") or ""
                ttype = tr.get("type") or ""
                if name.startswith("subtitles_text"):
                    tr["absolute_index"] = 1_200_000
                elif name.startswith("srt2images_"):
                    tr["absolute_index"] = 1_199_000
                elif name == "voiceover":
                    tr["absolute_index"] = 900_000
                elif ttype == "text":
                    tr["absolute_index"] = 700_000 - idx
                elif ttype == "audio":
                    tr["absolute_index"] = 800_000 - idx
                elif ttype == "video":
                    tr["absolute_index"] = 600_000 - idx

        content_data = _json2.loads(content_path.read_text(encoding="utf-8"))
        info_data = _json2.loads(info_path.read_text(encoding="utf-8"))
        _assign(content_data.get("tracks"))
        _assign(info_data.get("tracks"))
        content_path.write_text(_json2.dumps(content_data, ensure_ascii=False, indent=2), encoding="utf-8")
        info_path.write_text(_json2.dumps(info_data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning(f"Failed to enforce absolute_index: {exc}")


def _apply_common_subtitle_style(draft_dir: Path) -> None:
    """
    Normalize subtitle track styling across channels (shared baseline).
    Source of truth: master_styles_v2.json style 'ch02_common_subtitle_v2'
    Fallbacks (if style not found): center align, width 0.82, line_spacing 0.021,
    white text, thin black border, no background, position y=-0.8, scale 1.0
    """
    try:
        # Load style from SSOT
        style = None
        try:
            resolver = StyleResolver(Path(__file__).resolve().parents[1] / "config" / "master_styles_v2.json")
            style = resolver.get_style("ch02_common_subtitle_v2") or resolver.get_style("jinsei_standard_v2")
        except Exception:
            style = None

        # Defaults
        cfg = {
            "line_max_width": 0.82,
            "line_spacing": 0.021,
            "text_size": 30,
            "font_size": 15,
            "border_width": 0.06,
            "border_color": "#000000",
            "text_color": "#FFFFFF",
            "background_style": 0,
            "background_color": "",
            "background_alpha": 0.0,
            "transform_y": -0.8,
        }

        # Override from SSOT if available
        if style:
            s = style.subtitle_style
            cfg["text_size"] = s.font_size_pt * style.platform_overrides.get("capcut", {}).get("subtitle", {}).get("font_scale_factor", 0.1)
            cfg["font_size"] = cfg["text_size"] / 2  # approximate
            cfg["line_spacing"] = 0.02
            cfg["text_color"] = s.text_color
            if s.stroke_enabled:
                cfg["border_color"] = s.stroke_color
                cfg["border_width"] = 0.06
            cfg["background_style"] = 0 if not s.background_enabled else 1
            cfg["background_color"] = s.background_color if s.background_enabled else ""
            cfg["background_alpha"] = s.background_opacity if s.background_enabled else 0.0
            cfg["line_max_width"] = 0.82
            cfg["transform_y"] = -s.position_y  # invert logical to CapCut

        for fname in ("draft_content.json", "draft_info.json"):
            path = draft_dir / fname
            if not path.exists():
                continue
            data = json.loads(path.read_text())
            tracks = [t for t in data.get("tracks", []) if t.get("name") == "subtitles_text"]
            if not tracks:
                continue

            sub_mat_ids = set()
            for t in tracks:
                for seg in t.get("segments", []):
                    mid = seg.get("material_id")
                    if mid:
                        sub_mat_ids.add(mid)
                    clip = seg.setdefault("clip", {})
                    clip.setdefault("transform", {})
                    clip["transform"]["x"] = 0.0
                    clip["transform"]["y"] = cfg["transform_y"]
                    clip.setdefault("scale", {})
                    clip["scale"]["x"] = 1.0
                    clip["scale"]["y"] = 1.0

            mats = data.get("materials", {}).get("texts", [])
            for m in mats:
                if m.get("id") not in sub_mat_ids:
                    continue
                m["alignment"] = 1  # center
                m["line_max_width"] = cfg["line_max_width"]
                m["line_spacing"] = cfg["line_spacing"]
                m["text_size"] = cfg["text_size"]
                m["font_size"] = cfg["font_size"]
                m["border_width"] = cfg["border_width"]
                m["border_color"] = cfg["border_color"]
                m["text_color"] = cfg["text_color"]
                m["background_style"] = cfg["background_style"]
                m["background_color"] = cfg["background_color"]
                m["background_alpha"] = cfg["background_alpha"]
                m["force_apply_line_max_width"] = False

                content_field = m.get("content")
                if isinstance(content_field, str):
                    try:
                        c_json = json.loads(content_field)
                    except Exception:
                        c_json = None
                elif isinstance(content_field, dict):
                    c_json = content_field
                else:
                    c_json = None

                if isinstance(c_json, dict):
                    styles = c_json.get("styles") or []
                    for st in styles:
                        st["size"] = 3.0
                        fill = st.setdefault("fill", {})
                        fill["alpha"] = 1.0
                        fill_content = fill.setdefault(
                            "content", {"render_type": "solid", "solid": {"alpha": 1.0, "color": [1.0, 1.0, 1.0]}}
                        )
                        if isinstance(fill_content, dict):
                            solid = fill_content.setdefault("solid", {"alpha": 1.0, "color": [1.0, 1.0, 1.0]})
                            solid["alpha"] = 1.0
                            solid["color"] = [1.0, 1.0, 1.0]
                        st["strokes"] = st.get("strokes", [])
                        st["bold"] = False
                        st["underline"] = False
                    m["content"] = json.dumps(c_json, ensure_ascii=False)

            path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception as exc:  # pragma: no cover
        logger.warning(f"Subtitle style normalization failed: {exc}")


def _apply_channel_hook(channel_id: Optional[str], draft_dir: Path) -> None:
    """
    Run channel-specific post-processors if registered.
    """
    try:
        if not channel_id:
            return
        hook = CHANNEL_HOOKS.get(channel_id)
        if hook:
            # CH02では帯テキストを後から渡すことがあるため、必要ならここで引数を追加可能
            hook(draft_dir)
    except Exception as exc:  # pragma: no cover
        logger.warning(f"Channel hook failed for {channel_id}: {exc}")
def sync_draft_info_with_content(draft_dir: Path) -> bool:
    """
    Sync draft_info.json with draft_content.json.

    pyJianYingDraft 0.2.3 only updates draft_content.json, causing CapCut to ignore changes.
    This function syncs tracks and materials from draft_content to draft_info.

    Returns:
        True if sync successful, False otherwise
    """
    try:
        draft_content_path = draft_dir / "draft_content.json"
        draft_info_path = draft_dir / "draft_info.json"

        if not draft_content_path.exists() or not draft_info_path.exists():
            logger.warning("Cannot sync: draft_content.json or draft_info.json not found")
            return False

        # Load both files
        content_data = _json2.loads(draft_content_path.read_text(encoding='utf-8'))
        info_data = _json2.loads(draft_info_path.read_text(encoding='utf-8'))

        # Sync tracks
        content_tracks = content_data.get('tracks', [])
        info_tracks = info_data.get('tracks', [])

        # Build lookup of existing tracks by id for metadata preservation
        existing_by_id = {}
        for track in info_tracks:
            track_id = track.get('id')
            if track_id:
                existing_by_id[track_id] = track

        synced_tracks = []
        for idx, content_track in enumerate(content_tracks):
            track_id = content_track.get('id') or f'track_{idx}'

            base_track = existing_by_id.get(track_id, {})

            # Preserve commonly used optional metadata if present in existing track
            preserved_keys = (
                'absolute_index', 'render_index', 'z_index', 'is_default_name',
                'muted', 'locked', 'hidden', 'extra_info', 'track_style',
                'solo', 'group_id'
            )

            new_track = {
                'id': track_id,
                'type': content_track.get('type'),
                'name': content_track.get('name', base_track.get('name', '')),
                'attribute': content_track.get('attribute', base_track.get('attribute', 0)),
                'flag': content_track.get('flag', base_track.get('flag', 0)),
                'segments': copy.deepcopy(content_track.get('segments', []))
            }

            for key in preserved_keys:
                if key in base_track and key not in new_track:
                    new_track[key] = base_track[key]

            synced_tracks.append(new_track)

        info_data['tracks'] = synced_tracks

        # Sync materials (critical for CapCut to recognize new images/videos)
        content_materials = content_data.get('materials', {})
        info_data['materials'] = content_materials

        # Sync duration
        content_duration = content_data.get('duration', 0)
        info_data['duration'] = content_duration

        # Save updated draft_info.json
        draft_info_path.write_text(_json2.dumps(info_data, ensure_ascii=False, indent=2), encoding='utf-8')

        logger.info(f"✅ Synced draft_info.json: {len(synced_tracks)} tracks, {len(content_materials.get('videos', []))} video materials")

        return True

    except Exception as e:
        logger.error(f"Failed to sync draft_info.json: {e}")
        logger.debug(traceback.format_exc())
        return False


def _dedupe_tracks_and_materials(draft_dir: Path):
    """
    Remove duplicate tracks/materials caused by template carryover.
    Keep the last occurrence of each (type, name) track and dedupe video materials by path/material_name.
    """
    try:
        content_path = draft_dir / "draft_content.json"
        if not content_path.exists():
            return
        data = _json2.loads(content_path.read_text(encoding="utf-8"))
        tracks = data.get("tracks", [])
        deduped = []
        seen = set()
        for tr in reversed(tracks):
            key = (tr.get("type"), tr.get("name"))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(tr)
        deduped.reverse()
        data["tracks"] = deduped

        mats = data.get("materials", {}).get("videos", [])
        seen_vid = set()
        dedup_vid = []
        for m in reversed(mats):
            key = (m.get("path"), m.get("material_name"))
            if key in seen_vid:
                continue
            seen_vid.add(key)
            dedup_vid.append(m)
        dedup_vid.reverse()
        data.setdefault("materials", {})["videos"] = dedup_vid

        content_path.write_text(_json2.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _force_video_scale(draft_dir: Path, scale: float):
    """Force clip.scale for all video segments to the given scale (CapCut sometimes resets)."""
    try:
        content_path = draft_dir / "draft_content.json"
        if not content_path.exists():
            return
        data = _json2.loads(content_path.read_text(encoding="utf-8"))
        changed = False
        for tr in data.get("tracks", []):
            if tr.get("type") != "video":
                continue
            for seg in tr.get("segments", []):
                clip = seg.setdefault("clip", {})
                clip.setdefault("scale", {})
                clip.setdefault("transform", {})
                clip["scale"]["x"] = scale
                clip["scale"]["y"] = scale
                changed = True
        if changed:
            content_path.write_text(_json2.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def apply_auto_fade_transitions(draft_dir: Path, track_name: str, fade_duration_sec: float) -> int:
    """Inject crossfade transitions directly into CapCut draft JSON files."""
    if (
        _auto_add_crossfade_transitions is None
        or _auto_load_fade_json is None
        or _auto_save_fade_json is None
        or _auto_backup_file is None
        or _auto_sync_draft_info is None
    ):
        logger.debug("Auto fade helper unavailable; skipping fade injection")
        return 0

    if fade_duration_sec <= 0:
        logger.debug("Fade duration <= 0; skipping fade injection")
        return 0

    draft_content_path = draft_dir / "draft_content.json"
    if not draft_content_path.exists():
        logger.debug("draft_content.json missing; skipping fade injection")
        return 0

    try:
        draft_data = _auto_load_fade_json(draft_content_path)
    except Exception as exc:
        logger.warning(f"Failed to load draft_content.json for fade injection: {exc}")
        return 0

    try:
        added = _auto_add_crossfade_transitions(draft_data, fade_duration_sec, track_name)
    except Exception as exc:
        logger.warning(f"Failed to compute fade transitions: {exc}")
        return 0

    if added <= 0:
        return 0

    try:
        _auto_backup_file(draft_content_path)
        _auto_save_fade_json(draft_content_path, draft_data)
        _auto_sync_draft_info(draft_dir, draft_data)
        logger.info("✨ 自動フェードを適用しました: %d 個 (%.2fs)", added, fade_duration_sec)
    except Exception as exc:
        logger.warning(f"Failed to persist fade transitions: {exc}")
        return 0

    return added


def apply_belt_config(belt_data, opening_offset, draft_dir, logger, title=None, layout_config=None, channel_id=None):
    """
    帯レイヤーを上書き・最上位に移動（チャンネル共通）。
    CH01 固定のスタイル参照を排除し、レイアウトは belt_config / layout_config を優先。
    """
    try:
        # 汎用デフォルト
        slope = 0.022479
        intercept = -0.944752
        clip_y = 0.775
        scale_val = 0.236588

        # Dynamic layout override (SSOT)
        if layout_config:
            # CapCut coordinate system: Top=-1.0, Bottom=1.0, Center=0.0
            # beltTopPct: 0% = Top, 100% = Bottom
            # y = (pct / 50.0) - 1.0
            if hasattr(layout_config, 'beltTopPct'):
                pct = float(layout_config.beltTopPct)
                clip_y = (pct / 50.0) - 1.0
                logger.info(f"  📐 Applied dynamic layout: belt_y={clip_y:.3f} (from {pct}%)")

        def calculate_belt_x_position(text):
            char_count = len(text)
            x = slope * char_count + intercept
            return max(-1.0, min(1.0, x))

        draft_content_path = draft_dir / 'draft_content.json'
        import json as _json_belt
        content_data = _json_belt.loads(draft_content_path.read_text(encoding='utf-8'))

        SEC = 1_000_000
        opening_offset_us = int(opening_offset * SEC)
        total_duration_sec = belt_data.get('total_duration')
        if total_duration_sec is None:
            total_duration_sec = (content_data.get('duration', 0) / SEC)
        total_duration_us = int(total_duration_sec * SEC)

        # belt_dataの構造を処理（ベルトなしは安全にスキップ）
        belts_raw = belt_data.get('belts')
        belt_upper = belt_data.get('belt_upper', [])
        belt_lower = belt_data.get('belt_lower', {})

        if belts_raw is not None:
            def _normalize_belts(belts_list):
                """Sort by start, fill missing end/duration, ensure contiguous coverage."""
                if not belts_list:
                    return []
                belts_list = sorted(belts_list, key=lambda b: float(b.get('start', 0)))
                norm = []
                for idx, b in enumerate(belts_list):
                    start = float(b.get('start', 0.0))
                    end = b.get('end')
                    dur = b.get('duration')
                    if end is None and dur is not None:
                        end = start + float(dur)
                    norm.append({'text': b.get('text', ''), 'start': start, 'end': end})

                # fill gaps and ends
                for i in range(len(norm)):
                    if i < len(norm) - 1:
                        if norm[i]['end'] is None or norm[i]['end'] > norm[i+1]['start']:
                            norm[i]['end'] = norm[i+1]['start']
                    else:
                        if norm[i]['end'] is None:
                            norm[i]['end'] = total_duration_sec
                return norm

            belts = _normalize_belts(belts_raw)
            belt_upper = []
            for belt in belts:
                duration = max(0.0, float(belt['end']) - float(belt['start']))
                belt_upper.append({
                    'text': belt.get('text', ''),
                    'start_sec': float(belt.get('start', 0.0)),
                    'duration_sec': duration
                })

        fallback_title = title or belt_data.get("main_title") or ""
        if not belt_lower and fallback_title:
            belt_lower = {'text': fallback_title, 'start_sec': 0.0, 'duration_sec': total_duration_sec}
        else:
            belt_lower.setdefault('text', fallback_title)
            belt_lower.setdefault('start_sec', 0.0)
            belt_lower.setdefault('duration_sec', total_duration_sec)

        # ベルト情報が皆無かつタイトルも無い場合のみスキップ（落ちないようガード）
        if not belt_upper and (not belt_lower or not belt_lower.get('text')):
            logger.info("  ⏭️  belt_config has no belts/main title; skipping belt overlay")
            return

        tracks = content_data.get('tracks', [])
        materials_text = {mat.get('id'): mat for mat in content_data.get('materials', {}).get('texts', [])}

        def _segment_texts(segments):
            texts = []
            for seg in segments:
                mat_id = seg.get('material_id')
                mat = materials_text.get(mat_id)
                if not mat:
                    texts.append('')
                    continue
                import json as _json_text
                texts.append(_json_text.loads(mat['content']).get('text', ''))
            return texts

        target_upper_texts = [item['text'] for item in belt_upper]
        target_lower_text = belt_lower.get('text', '')

        disable_subtitle_belt = True  # 全チャンネルでサブ帯を無効化
        texts = list(materials_text.values())

        subtitle_belt_track_idx = None
        main_belt_track_idx = None

        if belt_upper and not disable_subtitle_belt:
            subtitle_candidate_idx = None
            main_candidate_idx = None
            for idx, track in enumerate(tracks):
                if track.get('type') != 'text':
                    continue
                segs = track.get('segments', [])
                seg_count = len(segs)
                seg_texts = _segment_texts(segs)
                if seg_count == len(target_upper_texts):
                    if set(seg_texts) == set(target_upper_texts):
                        subtitle_belt_track_idx = idx
                        logger.info(f"  ✅ サブタイトル帯発見: Track {idx}")
                    elif subtitle_candidate_idx is None:
                        subtitle_candidate_idx = idx
                elif seg_count == 1:
                    text_value = seg_texts[0] if seg_texts else ''
                    if target_lower_text and text_value == target_lower_text:
                        main_belt_track_idx = idx
                        logger.info(f"  ✅ メイン帯発見: Track {idx}")
                    elif main_candidate_idx is None:
                        main_candidate_idx = idx

            if subtitle_belt_track_idx is None:
                subtitle_belt_track_idx = subtitle_candidate_idx
            if main_belt_track_idx is None:
                main_belt_track_idx = main_candidate_idx
        else:
            if belt_upper:
                logger.info("  ⏭️ サブ帯はグローバル設定で無効化しました")
            else:
                logger.info("  ⏭️ サブ帯は指定なしのためスキップ")

        # メイン帯用のトラックを探す（title/belt系の名前を優先）。無ければ新規作成し、字幕は触らない。
        if main_belt_track_idx is None:
            for idx, track in enumerate(tracks):
                if track.get('type') != 'text':
                    continue
                name = (track.get('name') or '').lower()
                if 'subtitle' in name:
                    continue
                if 'title' in name or 'belt' in name:
                    main_belt_track_idx = idx
                    break
        if main_belt_track_idx is None:
            for idx, track in enumerate(tracks):
                if track.get('type') != 'text':
                    continue
                if len(track.get('segments', [])) == 1:
                    name = (track.get('name') or '').lower()
                    if 'subtitle' in name:
                        continue
                    main_belt_track_idx = idx
                    break
        if main_belt_track_idx is None:
            # 新規帯トラックを作成（他のテキストは触らない）
            new_mat_id = "belt_main_text"
            new_seg = {
                "id": "belt_main_seg",
                "material_id": new_mat_id,
                "target_timerange": {"start": opening_offset_us, "duration": int(total_duration_sec * SEC)},
                "source_timerange": {"start": 0, "duration": int(total_duration_sec * SEC)},
                "render_timerange": {"start": 0, "duration": int(total_duration_sec * SEC)},
            }
            tracks.append({
                "id": "belt_main_track",
                "type": "text",
                "name": "belt_main",
                "absolute_index": 1_000_001,
                "segments": [new_seg],
            })
            # Ensure materials_text contains the new material
            materials_text[new_mat_id] = {
                "id": new_mat_id,
                "type": "text",
                "content": json.dumps({"text": belt_lower['text']}, ensure_ascii=False),
            }
            main_belt_track_idx = len(tracks) - 1
            logger.info("  ➕ 新規メイン帯トラックを追加 (belt_main)")
        if main_belt_track_idx is None:
            logger.error("  ❌ メイン帯用のテキストトラックが見つかりません")
            return

        # refresh texts after possible additions
        texts = list(materials_text.values())

        # サブタイトル帯を更新（有効時のみ）
        if belt_upper and not disable_subtitle_belt and subtitle_belt_track_idx is not None:
            subtitle_track = tracks[subtitle_belt_track_idx]
            if len(subtitle_track.get('segments', [])) < len(belt_upper):
                import copy as _copy
                while len(subtitle_track['segments']) < len(belt_upper):
                    if subtitle_track['segments']:
                        subtitle_track['segments'].append(_copy.deepcopy(subtitle_track['segments'][-1]))
                    else:
                        break
            if len(subtitle_track.get('segments', [])) > len(belt_upper):
                subtitle_track['segments'] = subtitle_track['segments'][:len(belt_upper)]

            for idx, (segment, chapter) in enumerate(zip(subtitle_track['segments'], belt_upper)):
                start_sec = chapter.get('start_sec', 0.0)
                duration_sec = chapter.get('duration_sec')
                if duration_sec is None:
                    end_sec = chapter.get('end_sec')
                    if end_sec is not None:
                        duration_sec = max(0.0, end_sec - start_sec)
                    else:
                        duration_sec = 0.0
                start_us = int((start_sec * SEC) + opening_offset_us)
                duration_us = int(duration_sec * SEC)
                segment['target_timerange'] = {
                    'start': start_us,
                    'duration': duration_us
                }
                segment['source_timerange'] = {
                    'start': 0,
                    'duration': duration_us
                }
                segment['render_timerange'] = {
                    'start': 0,
                    'duration': duration_us
                }

                text = chapter['text']
                x = calculate_belt_x_position(text)

                if 'clip' not in segment:
                    segment['clip'] = {}
                if 'transform' not in segment['clip']:
                    segment['clip']['transform'] = {}
                if 'scale' not in segment['clip']:
                    segment['clip']['scale'] = {}

                segment['clip']['transform']['x'] = x
                segment['clip']['transform']['y'] = clip_y
                segment['clip']['scale']['x'] = scale_val
                segment['clip']['scale']['y'] = scale_val

                material_id = segment.get('material_id')
                if material_id:
                    for mat in texts:
                        if mat.get('id') == material_id:
                            import json as _json_text
                            content_obj = _json_text.loads(mat['content'])
                            content_obj['text'] = chapter['text']
                            mat['content'] = _json_text.dumps(content_obj, ensure_ascii=False)
                            mat['alignment'] = 1
                            mat['fixed_width'] = -1.0
                            mat['background_width'] = 0.28
                            mat['background_height'] = 0.28
                            mat['background_color'] = '#ffdc00'
                            mat['background_round_radius'] = 0.4
                            mat['background_alpha'] = 1.0
                            logger.info(f"  📝 章{idx+1}: {chapter['text']} (x={x:.4f})")
                            break

        # メイン帯を更新
        main_track = tracks[main_belt_track_idx]
        segment = main_track['segments'][0]
        lower_start_sec = float(belt_lower.get('start_sec', 0.0))
        lower_duration_sec = float(belt_lower.get('duration_sec', total_duration_sec))
        lower_start_us = int((lower_start_sec * SEC) + opening_offset_us)
        lower_duration_us = int(max(0.0, lower_duration_sec) * SEC)
        segment['target_timerange'] = {
            'start': lower_start_us,
            'duration': lower_duration_us
        }
        segment['source_timerange'] = {
            'start': 0,
            'duration': lower_duration_us
        }
        segment['render_timerange'] = {
            'start': 0,
            'duration': lower_duration_us
        }

        material_id = segment.get('material_id')
        if material_id:
            for mat in texts:
                if mat.get('id') == material_id:
                    import json as _json_text
                    content_obj = _json_text.loads(mat['content'])
                    content_obj['text'] = belt_lower['text']
                    mat['content'] = _json_text.dumps(content_obj, ensure_ascii=False)
                    logger.info(f"  📝 メイン帯: {belt_lower['text'][:30]}...")
                    break

        # トラックを最上位に移動（サブ帯なしならメインのみ）
        if subtitle_belt_track_idx is not None and belt_upper and not disable_subtitle_belt:
            subtitle_track_obj = tracks.pop(subtitle_belt_track_idx)
            if main_belt_track_idx > subtitle_belt_track_idx:
                main_belt_track_idx -= 1
            main_track_obj = tracks.pop(main_belt_track_idx)
            tracks.append(subtitle_track_obj)
            tracks.append(main_track_obj)
        else:
            main_track_obj = tracks.pop(main_belt_track_idx)
            tracks.append(main_track_obj)
        logger.info("  ✅ 帯トラックを最上位に移動")

        # 保存（materials.textsも更新）
        content_data['tracks'] = tracks
        if "materials" not in content_data:
            content_data["materials"] = {}
        existing = {m.get("id"): m for m in content_data["materials"].get("texts", [])}
        for m in texts:
            if m.get("id"):
                existing[m["id"]] = m
        content_data["materials"]["texts"] = list(existing.values())
        draft_content_path.write_text(_json_belt.dumps(content_data, ensure_ascii=False, indent=2))
        logger.info("  ✅ 帯トラック更新完了")

    except Exception as e:
        logger.error(f"帯トラック更新失敗: {e}")
        import traceback
        logger.debug(traceback.format_exc())


def adjust_effect_duration(script, total_duration_sec, draft_dir, logger):
    """
    エフェクトトラックのエンド位置を調整

    エフェクトトラックの最初のセグメントのdurationを動画の長さに合わせる
    """
    try:
        draft_content_path = draft_dir / 'draft_content.json'
        import json as _json_effect
        content_data = _json_effect.loads(draft_content_path.read_text(encoding='utf-8'))

        SEC = 1_000_000
        total_duration_us = int(total_duration_sec * SEC)

        tracks = content_data.get('tracks', [])
        effect_track_idx = None

        # エフェクトトラックを探す
        for idx, track in enumerate(tracks):
            track_type = track.get('type', '')
            if track_type == 'effect':
                effect_track_idx = idx
                logger.info(f"  ✅ エフェクトトラック発見: Track {idx}")
                break

        # エフェクトの終了位置を調整
        if effect_track_idx is not None:
            track = tracks[effect_track_idx]
            segments = track.get('segments', [])

            if segments:
                # 最初のセグメントのdurationを調整
                segment = segments[0]
                old_duration = segment.get('target_timerange', {}).get('duration', 0)
                segment['target_timerange']['duration'] = total_duration_us

                old_sec = old_duration / SEC
                logger.info(f"  ⏱️  エフェクト終了位置調整: {old_sec:.1f}s → {total_duration_sec:.1f}s")

                # material_timerangeも調整（エフェクト素材の長さ）
                if 'material_timerange' in segment:
                    segment['material_timerange']['duration'] = total_duration_us

        # 更新されたコンテンツを保存
        draft_content_path.write_text(_json_effect.dumps(content_data, ensure_ascii=False, indent=2))
        logger.info("  ✅ エフェクト調整完了")

    except Exception as e:
        logger.error(f"エフェクト調整失敗: {e}")
        import traceback
        logger.debug(traceback.format_exc())


def fix_subtitle_style_direct(draft_info_path: str, adapter: CapCutStyleAdapter):
    """
    Fixes subtitle styles directly in draft_content.json using the provided adapter.
    This is necessary because pyJianYingDraft has limitations with specific background offsets.
    """
    try:
        draft_content_path = Path(draft_info_path).parent / "draft_content.json"
        if not draft_content_path.exists():
            logger.warning(f"draft_content.json not found at {draft_content_path}")
            return

        with open(draft_content_path, 'r', encoding='utf-8') as f:
            content = json.load(f)

        # Get authoritative config from adapter
        config = adapter.get_subtitle_config()
        
        # 1. Update Backgrounds (Canvas Colors)
        # Only if background is enabled in the style
        bg_config = config.get("background")
        if bg_config:
            materials = content.get("materials", {})
            canvas_colors = materials.get("canvas_colors", [])
            
            # The adapter returns a config dict suitable for pyJianYingDraft/Internal JSON
            # We need to map it to the raw JSON structure of CapCut
            
            for cc in canvas_colors:
                # Identify if this is a subtitle background (usually by checking usage or ID)
                # For now, we assume ALL text backgrounds in this draft should follow the style
                # Or simpler: we update the attributes that match our target style
                
                # Update offsets (Critical for Jinsei style)
                cc["color"] = list(adapter._hex_to_rgb_tuple(bg_config["color"])) + [bg_config["alpha"]] # RGB + Alpha
                
                # CapCut JSON uses 'width' and 'height' for size ratio
                cc["width"] = bg_config["width"]
                cc["height"] = bg_config["height"]
                
                # Offsets
                cc["transform_x"] = bg_config["horizontal_offset"]
                cc["transform_y"] = bg_config["vertical_offset"]
                
                # Radius
                cc["radius"] = bg_config["round_radius"]

        # 2. Update Text Styles
        # ... (Implementation for styles update if needed, though pyJianYingDraft usually handles this)
        # But since we are here, let's ensure specific attributes that pyJianYingDraft might miss
        
        # Save changes
        with open(draft_content_path, 'w', encoding='utf-8') as f:
            json.dump(content, f, indent=4, ensure_ascii=False)
        
        logger.info(f"Applied direct JSON fixes from Adapter to {draft_content_path}")

    except Exception as e:
        logger.error(f"Failed to apply direct subtitle fixes: {e}")
        traceback.print_exc()


def _ensure_timerange(seg, default_duration_us: int = 1_000_000):
    """
    Normalize timerange fields on a segment. Handles cases where pyJianYingDraft
    stores string timeranges that break export_json.
    """
    try:
        for field in ("target_timerange", "source_timerange", "render_timerange"):
            val = getattr(seg, field, None)
            # If Timerange object with duration, leave as is
            if hasattr(val, "duration") and hasattr(val, "start"):
                continue
            # If dict-like with duration, keep
            if isinstance(val, dict) and "duration" in val:
                continue
            # Otherwise coerce to dict
            setattr(seg, field, {"start": 0, "duration": int(default_duration_us)})
    except Exception:
        pass


def _sanitize_all_timeranges(script, fallback_us: int = 1_000_000):
    """
    Ensure all segments across all tracks have dict or Timerange timeranges, never str.
    """
    try:
        for track in getattr(script, "tracks", {}).values():
            for seg in getattr(track, "segments", []):
                dur = fallback_us
                try:
                    tt = getattr(seg, "target_timerange", None)
                    if isinstance(tt, dict):
                        dur = tt.get("duration", dur)
                    elif hasattr(tt, "duration"):
                        dur = getattr(tt, "duration", dur)
                except Exception:
                    pass
                _ensure_timerange(seg, default_duration_us=dur)
    except Exception:
        pass


def _shift_existing_segments(script, offset_us: int):
    """
    Shift all existing segments in the template by offset_us (start += offset_us).
    Durationは維持。target/source/render_timerange を揃えてずらす。
    """
    try:
        if offset_us <= 0:
            return
        for tr in getattr(script, "tracks", {}).values():
            # Audio/textは前詰めのまま。ブランクは映像/エフェクトのみ後ろへ送る。
            try:
                if getattr(tr, "type", None) in (Track_type.audio, Track_type.text):
                    continue
            except Exception:
                pass
            for seg in getattr(tr, "segments", []):
                for field in ("target_timerange", "source_timerange", "render_timerange", "material_timerange"):
                    tt = getattr(seg, field, None)
                    if isinstance(tt, dict):
                        start = tt.get("start", 0) + offset_us
                        tt["start"] = start
                    elif hasattr(tt, "start"):
                        try:
                            tt.start = tt.start + offset_us
                        except Exception:
                            pass
    except Exception:
        logger.warning("Failed to shift existing segments by offset %s", offset_us)


def _shift_audio_in_json(draft_dir: Path, offset_us: int):
    """
    Post-process draft_content.json and draft_info.json to shift audio tracks
    so that any audio segment starting before offset_us is moved to offset_us.
    """
    if offset_us <= 0:
        return
    for fname in ["draft_content.json", "draft_info.json"]:
        path = draft_dir / fname
        if not path.exists():
            continue
        try:
            import json as _json
            data = _json.loads(path.read_text(encoding="utf-8"))
            modified = False
            for tr in data.get("tracks", []):
                if tr.get("type") != "audio":
                    continue
                for seg in tr.get("segments", []):
                    for field in ("target_timerange", "source_timerange", "render_timerange", "material_timerange"):
                        tt = seg.get(field)
                        if isinstance(tt, dict):
                            start = tt.get("start", 0)
                            if start < offset_us:
                                tt["start"] = start + offset_us
                                modified = True
            if modified:
                path.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to shift audio in %s: %s", fname, e)


def _shift_tracks_in_json(draft_dir: Path, offset_us: int):
    """
    Shift non-LLM tracks (template tracks) so that any segment starting before offset_us
    is moved to offset_us. Excludes our injected tracks (srt2images_*, subtitles_text, title_text).
    """
    if offset_us <= 0:
        return
    exclude_prefixes = ("srt2images_", "subtitles_text", "title_text")
    for fname in ["draft_content.json", "draft_info.json"]:
        path = draft_dir / fname
        if not path.exists():
            continue
        try:
            import json as _json
            data = _json.loads(path.read_text(encoding="utf-8"))
            modified = False
            for tr in data.get("tracks", []):
                name = tr.get("name") or ""
                if any(name.startswith(p) for p in exclude_prefixes):
                    continue
                if tr.get("type") not in ("video", "effect"):
                    continue
                for seg in tr.get("segments", []):
                    for field in ("target_timerange", "source_timerange", "render_timerange", "material_timerange"):
                        tt = seg.get(field)
                        if isinstance(tt, dict):
                            start = tt.get("start", 0)
                            if start < offset_us:
                                tt["start"] = start + offset_us
                                modified = True
            if modified:
                path.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to shift tracks in %s: %s", fname, e)


def _validate_opening_offset(draft_dir: Path, opening_offset_us: int, logger: logging.Logger, tolerance_us: int = 10_000):
    """
    Ensure non-exempt tracks do not start before opening_offset_us.
    Exempt:
      - audio (BGM想定)
      - subtitles_text
      - video tracks with many tiny segments (背景トラック: segments >= 30)
    """
    try:
        content_path = draft_dir / "draft_content.json"
        if not content_path.exists() or opening_offset_us <= 0:
            return
        import json as _json
        data = _json.loads(content_path.read_text(encoding="utf-8"))
        offenders = []
        tracks = data.get("tracks", [])
        for tr in tracks:
            ttype = tr.get("type")
            name = (tr.get("name") or "").lower()
            segs = tr.get("segments") or []

            # Exemptions
            if ttype == "audio":
                continue  # BGMなど
            if name.startswith("subtitles_text") or "subtitle" in name:
                continue
            if ttype == "video" and len(segs) >= 30:
                continue  # 背景トラック

            for seg in segs:
                tt = seg.get("target_timerange") or {}
                start = tt.get("start", 0)
                if start + tolerance_us < opening_offset_us:
                    offenders.append({
                        "track": tr.get("name") or tr.get("id") or "",
                        "type": ttype,
                        "start_ms": round(start / 1000, 1),
                        "offset_ms": round(opening_offset_us / 1000, 1),
                    })
                    break  # 1個見つけたら十分

        if offenders:
            logger.error("❌ 開始オフセット違反: %d トラックが %0.3fs 未満で開始しています", len(offenders), opening_offset_us / 1_000_000)
            for off in offenders:
                logger.error("  - %s (%s) start=%sms expected>=%sms",
                             off["track"] or "(no-name)", off["type"],
                             off["start_ms"], off["offset_ms"])
            sys.exit(1)
    except Exception as e:
        logger.warning(f"Opening offset validation skipped: {e}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="srt2images output run dir (contains image_cues.json and images/)")
    ap.add_argument("--draft-root", default=str(Path.home() / "Movies/CapCut/User Data/Projects/com.lveditor.draft"))
    ap.add_argument("--channel", help="Channel ID (e.g., CH01) to auto-apply template/position presets")
    # Template: leave empty to use channel preset
    ap.add_argument("--template", required=False, default="", help="Existing CapCut draft folder name to duplicate (if empty, channel preset is used)")
    ap.add_argument("--preferred-template-prefix", default="", help="When exact template name not found, pick the newest starting with this prefix (optional)")
    ap.add_argument("--new", default="5-コピー-画像版", help="New draft folder name")
    ap.add_argument("--transition", default="Dissolve")
    ap.add_argument("--crossfade", type=float, default=0.5)
    ap.add_argument("--fade-duration", type=float, default=None, help="Fade duration between CapCut image segments (defaults to --crossfade)")
    ap.add_argument("--disable-auto-fade", action="store_true", help="Skip automatic CapCut fade injection")
    # Position/scale defaults (corrected for exact user coordinates: X=-313, Y=217, Scale=59%)
    ap.add_argument("--tx", type=float, default=-0.3125, help="transform_x (half-canvas units)")
    # NOTE: In CapCut, positive transform_y moves UP. UI Y=+pixels (down) => negative transform_y
    ap.add_argument("--ty", type=float, default=0.20555555555, help="transform_y (half-canvas units)")
    ap.add_argument("--scale", type=float, default=1.03)
    ap.add_argument("--title", help="Left-top title text to set")
    ap.add_argument("--title-duration", type=float, default=30.0, help="Title display duration (seconds)")
    ap.add_argument("--skip-title", action="store_true", help="Skip title insertion (useful when a JSON post-processor will inject it)")
    ap.add_argument("--srt-file", help="SRT file path for subtitle insertion")
    ap.add_argument("--inject-into-main", action="store_true", help="Insert into the first existing video track instead of creating a new one")
    ap.add_argument("--rank-from-top", type=int, default=4, help="Desired layer rank from top (1=topmost). Images will be placed on this layer rank.")
    ap.add_argument("--voice-file", help="Voice audio file (WAV/MP3) to insert below BGM (optional)")
    ap.add_argument("--opening-offset", type=float, default=3.0, help="Opening duration offset (seconds) - all elements start after this (default: 3.0)")
    ap.add_argument("--belt-config", help="Belt layer config JSON file (belt_config.json)")
    ap.add_argument("--validate-only", action="store_true", help="Validate inputs without creating draft (dry-run mode)")
    args = ap.parse_args()

    if args.fade_duration is None:
        args.fade_duration = args.crossfade

    resolver = ChannelPresetResolver()
    parser_defaults = {
        "template": ap.get_default("template"),
        "preferred_template_prefix": ap.get_default("preferred_template_prefix"),
        "tx": ap.get_default("tx"),
        "ty": ap.get_default("ty"),
        "scale": ap.get_default("scale"),
        "opening_offset": ap.get_default("opening_offset"),
    }
    detected_channel = args.channel
    if not detected_channel:
        candidate_sources = [args.srt_file, args.run]
        for candidate in candidate_sources:
            detected_channel = infer_channel_id_from_path(candidate)
            if detected_channel:
                break
    
    # 2. Resolve Channel Configuration
    channel_id = args.channel or detected_channel
    preset = resolver.resolve(channel_id)
    
    if preset:
        # Auto-apply preset values to args if they are defaults/empty
        if preset.capcut_template and (not args.template or args.template == parser_defaults["template"]):
            args.template = preset.capcut_template
        if preset.capcut_template and (not args.preferred_template_prefix or args.preferred_template_prefix == parser_defaults["preferred_template_prefix"]):
            args.preferred_template_prefix = preset.capcut_template

        if preset.position:
            for axis in ("tx", "ty", "scale"):
                if getattr(args, axis) == parser_defaults[axis]:
                    setattr(args, axis, preset.position.get(axis))

    # Re-resolve strict preset object for later use
    if not preset and channel_id:
         # Fallback if resolve(channel_id) failed above or was not called correctly
         preset = resolver.resolve(channel_id)

    if not preset:
        logger.warning(f"Could not resolve channel preset for ID: {channel_id}")
        # Continue without preset? Or return? 
        # Original code returned if channel_id was found but preset was None.
        if channel_id:
             logger.error("Aborting due to missing preset.")
             return

    if preset:
        logger.info(f"Using Channel Preset: {preset.name} ({preset.channel_id})")
    
    # 3. Resolve Video Style & Create Adapter
    style_resolver = StyleResolver()
    # Use ID from preset, or fallback to preset's embedded style
    style_id = preset.config_model.video_style_id if preset and preset.config_model else None
    
    # Fallback for legacy presets without video_style_id (enforce migration path)
    if not style_id and channel_id == "CH01":
        style_id = "jinsei_standard_v2"
    
    video_style = style_resolver.resolve_from_preset(style_id)
    adapter = CapCutStyleAdapter(video_style)
    logger.info(f"Resolved Video Style: {video_style.name}")

    # Get global timeline settings from adapter
    timeline_settings = adapter.get_timeline_settings()
    OPENING_OFFSET = timeline_settings.get("opening_offset_sec", 3.0)
    # If channel preset belt defines opening_offset, prefer it
    try:
        if preset and preset.belt and "opening_offset" in preset.belt:
            OPENING_OFFSET = float(preset.belt.get("opening_offset", OPENING_OFFSET))
    except Exception:
        pass
    
    # Override args if not manually set
    if args.opening_offset == parser_defaults["opening_offset"]:
        args.opening_offset = float(OPENING_OFFSET)


    # ========================================
    # 🛡️ PRE-FLIGHT CHECK - Fail Fast
    # ========================================
    logger.info("🔍 Running pre-flight checks...")
    validation_errors = pre_flight_check(args, logger)

    if validation_errors:
        logger.error("\n❌ Pre-flight check FAILED:")
        for error in validation_errors:
            logger.error(error)
        sys.exit(1)

    logger.info("✅ Pre-flight checks passed")

    if args.validate_only:
        logger.info("✅ Validation successful (--validate-only mode, exiting)")
        sys.exit(0)

    run_dir = Path(args.run).resolve()
    images_dir = run_dir / "images"
    fps, size, crossfade_from_run, cues = load_cues(run_dir)
    crossfade = args.crossfade if args.crossfade is not None else crossfade_from_run

    # Calculate opening offset in microseconds
    opening_offset_us = int(args.opening_offset * 1_000_000)

    # Build absolute schedule from cues' start/end seconds (do not force contiguous)
    schedule = make_absolute_schedule_us(cues, offset_us=opening_offset_us)
    width, height = size["width"], size["height"]

    # Duplicate draft (template already validated by pre_flight_check)
    df = Draft_folder(args.draft_root)
    template_name = args.template
    logger.info(f"📋 Using template: {template_name}")

    try:
        df.duplicate_as_template(template_name, args.new, allow_replace=True)
        logger.info(f"✅ Duplicated template to: {args.new}")
    except Exception as e:
        logger.error(f"❌ Failed to duplicate template '{template_name}': {e}")
        logger.debug(traceback.format_exc())
        raise
    script = df.load_template(args.new)
    draft_dir = Path(args.draft_root) / args.new
    assets_dir = draft_dir / 'assets' / 'image'
    assets_dir.mkdir(parents=True, exist_ok=True)

    # Shift existing template segments to honor opening_offset (e.g., CH01 first 3s blank)
    if opening_offset_us > 0:
        _shift_existing_segments(script, opening_offset_us)

    # Ensure original cue metadata is available inside the CapCut draft
    image_cues_src = run_dir / "image_cues.json"
    image_cues_dest = draft_dir / "image_cues.json"
    if image_cues_src.exists():
        try:
            shutil.copy2(image_cues_src, image_cues_dest)
            logger.info(f"📝 Copied image_cues.json to draft: {image_cues_dest}")
        except Exception as exc:
            logger.warning(f"⚠️ Failed to copy image_cues.json to draft: {exc}")
    else:
        logger.warning(f"⚠️ image_cues.json not found in run dir: {image_cues_src}")

    # Decide target track
    if args.inject_into_main:
        # Try to find an existing primary video track
        target_track = None
        try:
            for name, tr in getattr(script, 'tracks', {}).items():
                if hasattr(tr, 'type') and tr.type == Track_type.video:
                    target_track = name
                    break
        except Exception:
            target_track = None
        if not target_track:
            target_track = "main_video"
            desired_index = _compute_abs_index_for_rank(draft_dir, args.rank_from_top)
            ensure_video_track(script, name=target_track, absolute_index=desired_index)
        track_name = target_track
        try:
            script.tracks[track_name].segments = []
        except Exception:
            pass
    else:
        base = f"srt2images_{run_dir.name}"
        track_name = base
        # Remove any existing srt2images_* video tracks to avoid duplicates
        try:
            for name in list(getattr(script, 'tracks', {}).keys()):
                tr = script.tracks[name]
                if hasattr(tr, 'type') and tr.type == Track_type.video and name.startswith(base):
                    del script.tracks[name]
        except Exception:
            pass
        desired_index = _compute_abs_index_for_rank(draft_dir, args.rank_from_top)
        ensure_video_track(script, name=track_name, absolute_index=desired_index)
        # Clear our track if it already has segments
        try:
            script.tracks[track_name].segments = []
        except Exception:
            pass

    # Clean previous assets/materials to avoid duplication from template
    try:
        if assets_dir.exists():
            shutil.rmtree(assets_dir)
        assets_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    try:
        # pyJianYingDraft Script keeps materials as dict; clear videos to avoid doubling
        if hasattr(script, "materials") and isinstance(script.materials, dict):
            script.materials["videos"] = []
    except Exception:
        pass

    # Insert images
    prev_end_us = None
    for i, cue in enumerate(cues):
        img = images_dir / f"{i+1:04d}.png"
        dest = assets_dir / img.name
        try:
            shutil.copy2(img, dest)
        except Exception:
            pass
        # Absolute timing from cues
        start_us, dur_us = schedule[i]
        # Material: reference draft-local asset path to avoid relinking
        if _VIDEO_MATERIAL_REQUIRES_TYPE:
            mat = Video_material(material_type='photo', path=str(dest), material_name=img.name)
        else:
            mat = Video_material(path=str(dest), material_name=img.name)
        # Register material into the draft to ensure materials.images contains it
        try:
            script.add_material(mat)
        except Exception as e:
            print(f"Warning: Failed to add_material for {img.name}: {e}")

        seg = Video_segment(
            mat,
            target_timerange=Timerange(start_us, dur_us),
            source_timerange=Timerange(0, dur_us),
            clip_settings=Clip_settings(transform_x=args.tx, transform_y=args.ty, scale_x=args.scale, scale_y=args.scale),
        )

        # Gentle Ken Burns drift（安全マージン確保: 基本スケール1.03固定＋微小移動）
        try:
            if 'clip' not in seg.__dict__:
                seg.clip = {}
            seg.clip.setdefault('transform', {})
            seg.clip.setdefault('scale', {})

            rng = random.Random(7739 + i)  # deterministic per index
            pos_jitter = 0.02  # small move to avoid cropping
            start_tx = args.tx + rng.uniform(-pos_jitter, pos_jitter)
            start_ty = args.ty + rng.uniform(-pos_jitter, pos_jitter)
            end_tx = args.tx + rng.uniform(-pos_jitter, pos_jitter)
            end_ty = args.ty + rng.uniform(-pos_jitter, pos_jitter)
            # scale: keep base 1.03, end up to +2%
            start_scale = args.scale
            end_scale = args.scale * (1.0 + rng.uniform(0.0, 0.02))

            seg.clip['transform']['x'] = start_tx
            seg.clip['transform']['y'] = start_ty
            seg.clip['scale']['x'] = start_scale
            seg.clip['scale']['y'] = start_scale

            seg.add_keyframe(KeyframeProperty.position_x, 0, start_tx)
            seg.add_keyframe(KeyframeProperty.position_y, 0, start_ty)
            seg.add_keyframe(KeyframeProperty.uniform_scale, 0, start_scale)
            seg.add_keyframe(KeyframeProperty.position_x, dur_us, end_tx)
            seg.add_keyframe(KeyframeProperty.position_y, dur_us, end_ty)
            seg.add_keyframe(KeyframeProperty.uniform_scale, dur_us, end_scale)
        except Exception:
            pass
        # Apply transition to current segment (CapCut applies transition at boundary; clips should not overlap)
        # Disabled due to pyJianYingDraft 0.2.x API changes
        # if args.transition and crossfade > 0 and i > 0 and prev_end_us is not None and abs(start_us - prev_end_us) < int(0.02 * SEC):
        #     try:
        #         ttype = getattr(draft.CapCut_Transition_type, args.transition)
        #     except Exception:
        #         ttype = getattr(draft.Transition_type, args.transition)
        #     seg.add_transition(ttype, duration=int(crossfade * 1e6))

        script.add_segment(seg, track_name=track_name)
        prev_end_us = start_us + dur_us

    # Add title text if specified
    if args.title and not getattr(args, "skip_title", False):
        try:
            title_track_name = "title_text"
            title_track = None
            # Prefer an existing 'title_text' track; ignore others (字幕を壊さない)
            if title_track_name in script.tracks:
                title_track = script.tracks[title_track_name]
            else:
                # Create dedicated title track
                script.add_track(Track_type.text, title_track_name, absolute_index=1_000_000)
                title_track = script.tracks.get(title_track_name)

            if title_track is not None:
                # Ensure one segment
                if not getattr(title_track, "segments", None):
                    title_track.segments = []
                if not title_track.segments:
                    try:
                        tr = Timerange(opening_offset_us, int(args.title_duration * SEC))
                        seg = Text_segment(args.title, tr)
                        title_track.segments.append(seg)
                        print(f"Added new title: '{args.title}'")
                    except Exception:
                        print(f"Warning: Failed to build title segment for '{args.title}' (constructor mismatch)")
                else:
                    first_segment = title_track.segments[0]
                    if hasattr(first_segment, 'text'):
                        print(f"Updated title from '{getattr(first_segment, 'text', '')}' to '{args.title}'")
                        first_segment.text = args.title
                        try:
                            td = int(args.title_duration * SEC)
                            timerange_dict = {"start": opening_offset_us, "duration": td}
                            if not getattr(first_segment, "target_timerange", None) or isinstance(first_segment.target_timerange, str):
                                first_segment.target_timerange = timerange_dict
                            if not getattr(first_segment, "source_timerange", None) or isinstance(first_segment.source_timerange, str):
                                first_segment.source_timerange = timerange_dict
                            if not getattr(first_segment, "render_timerange", None) or isinstance(first_segment.render_timerange, str):
                                first_segment.render_timerange = timerange_dict
                        except Exception:
                            pass
        except Exception as e:
            print(f"Warning: Failed to set title '{args.title}': {e}")

    # Add SRT subtitles if specified - with 人生の道標 style design
    if args.srt_file:
        try:
            srt_path = Path(args.srt_file)
            if srt_path.exists():
                # Copy SRT file to draft directory for reference
                draft_path = Path(args.draft_root) / args.new
                srt_dest = draft_path / f"{args.new}.srt"
                try:
                    shutil.copy2(srt_path, srt_dest)
                    print(f"Copied SRT file to {srt_dest} for CapCut to use directly")
                except Exception:
                    pass

                # Insert subtitles on a dedicated top text layer (above images)
                subs = parse_srt_file(srt_path)
                if subs:
                    sub_track_name = "subtitles_text"
                    try:
                        # Ensure a very high absolute index so text stays on top
                        script.add_track(Track_type.text, sub_track_name, absolute_index=2_000_000)
                    except Exception:
                        pass
                    # Clear existing
                    try:
                        script.tracks[sub_track_name].segments = []
                    except Exception:
                        pass
                    # Get subtitle settings from adapter (SSOT)
                    sub_config = adapter.get_subtitle_config()
                    
                    style_data = sub_config.get("style", {})
                    bg_data = sub_config.get("background")
                    border_data = sub_config.get("border")
                    pos_data = sub_config.get("position", {})

                    # Create Text_style
                    subtitle_style = Text_style(
                        size=style_data.get('size', 5.0),
                        color=style_data.get('color', (1.0, 1.0, 1.0)),
                        alpha=style_data.get('alpha', 1.0),
                        align=1,  # Center alignment
                        line_spacing=style_data.get('line_spacing', 0.02)
                    )

                    # Create Text_background
                    subtitle_background = None
                    if bg_data:
                        subtitle_background = Text_background(
                            color=tuple(bg_data.get('color')) if isinstance(bg_data.get('color'), (list, tuple)) else (0,0,0), # Adapter returns hex, fix needed? No, adapter returns tuple now in fix_subtitle_style_direct but let's check get_subtitle_config return type.
                            # Wait, adapter._hex_to_rgb_tuple returns tuple. But get_subtitle_config returns dict with 'color': '#FFFFFF' string? 
                            # Let's verify Adapter implementation.
                            # Adapter implementation: "color": self._hex_to_rgb_tuple(s.text_color) -> It returns TUPLE.
                            # So bg_data['color'] is already a tuple/list.
                            # Re-checking: "color": s.background_color (HEX STRING in bg_config) ? 
                            # In adapter code: "color": s.background_color (which is HEX str) 
                            # BUT in fix_subtitle_style_direct we did: list(adapter._hex_to_rgb_tuple(bg_config["color"]))
                            # So get_subtitle_config returns HEX string for background color.
                            # We need to convert it here.
                            
                            alpha=bg_data.get('alpha', 1.0),
                            round_radius=bg_data.get('round_radius', 0.4),
                            style=1,
                            height=bg_data.get('height', 0.28),
                            width=bg_data.get('width', 0.28),
                            horizontal_offset=bg_data.get('horizontal_offset', -1.0),
                            vertical_offset=bg_data.get('vertical_offset', -1.0)
                        )
                        # Manual color conversion if string
                        if isinstance(bg_data.get('color'), str):
                             subtitle_background.color = adapter._hex_to_rgb_tuple(bg_data['color'])

                    # Create Text_border
                    subtitle_border = None
                    if border_data:
                        subtitle_border = Text_border(
                            width=border_data.get('width', 0.08),
                            alpha=border_data.get('alpha', 1.0),
                            color=border_data.get('color', (0,0,0))
                        )

                    # Create Clip_settings for position
                    subtitle_clip = Clip_settings(
                        transform_x=pos_data.get('x', 0.0),
                        transform_y=pos_data.get('y', -0.8),
                        scale_x=1.0,
                        scale_y=1.0
                    )

                    added = 0
                    for ent in subs:
                        # 字幕は台本タイミング通り（冒頭ブランクは映像だけ）
                        start_us = int(ent['start_us'])
                        dur_us = max(SEC // 60, int(ent['end_us'] - ent['start_us']))
                        text_val = ent.get('text', '')
                        try:
            # Create text segment (generic style)
                            text_seg = Text_segment(
                                text_val,
                                Timerange(start_us, dur_us),
                                style=subtitle_style,
                                background=subtitle_background,
                                border=subtitle_border,
                                clip_settings=subtitle_clip
                            )
                            script.add_segment(text_seg, track_name=sub_track_name)
                            added += 1
                        except Exception as e:
                            print(f"Warning: Could not apply full style to segment: {e}")
                            continue
                    print(f"Inserted {added} subtitle segments on track '{sub_track_name}' with configured subtitle style")
                else:
                    print("Warning: Parsed 0 subtitle entries from SRT")
            else:
                print(f"Warning: SRT file not found: {srt_path}")

            # Normalize subtitle styling (shared baseline across channels)
            try:
                _apply_common_subtitle_style(Path(args.draft_root) / args.new)
                logger.info("Applied common subtitle style normalization")
            except Exception as exc:
                logger.warning(f"Subtitle style normalization failed: {exc}")
        except Exception as e:
            print(f"Warning: Failed to insert SRT subtitles: {e}")

    # Normalize timeranges for all text segments to avoid pyJianYingDraft export errors
    try:
        default_text_dur = int(args.title_duration * SEC) if hasattr(args, "title_duration") else SEC
        for tname, track in getattr(script, "tracks", {}).items():
            if getattr(track, "type", "") != "text":
                continue
            for seg in getattr(track, "segments", []):
                _ensure_timerange(seg, default_duration_us=default_text_dur)
    except Exception:
        logger.warning("Timerange normalization for text tracks failed (non-fatal)")

    # Insert voice audio if provided
    if getattr(args, 'voice_file', None):
        try:
            vpath = Path(args.voice_file)
            if vpath.exists():
                # Copy to materials/audio
                audio_dir = draft_dir / 'materials' / 'audio'
                audio_dir.mkdir(parents=True, exist_ok=True)
                voice_dest = audio_dir / vpath.name
                try:
                    shutil.copy2(vpath, voice_dest)
                except Exception:
                    pass
                # Ensure audio track below BGM
                voice_track = 'voiceover'
                voice_index = _compute_audio_voice_index_below_bgm(draft_dir, fallback=5)
                try:
                    script.add_track(Track_type.audio, voice_track, absolute_index=voice_index)
                except Exception:
                    pass
                # Determine total timeline duration from cues/schedule
                total_us = 0
                try:
                    for s, d in schedule:
                        total_us = max(total_us, s + d)
                except Exception:
                    total_us = int(60 * SEC)

                # Voice audio should match the actual content duration (without opening offset)
                # Since schedule already includes opening_offset_us, the voice track should start AT opening_offset_us
                
                try:
                    from pyJianYingDraft import Audio_material, Audio_segment
                    # Derive voice duration from wav length; fallback to total timeline minus opening offset
                    voice_duration_us = None
                    try:
                        import wave
                        with wave.open(str(vpath), "rb") as wf:
                            frames = wf.getnframes()
                            rate = wf.getframerate() or 1
                            voice_duration_us = int(frames / rate * SEC)
                    except Exception:
                        voice_duration_us = None
                    if not voice_duration_us or voice_duration_us <= 0:
                        voice_duration_us = max(0, total_us - opening_offset_us)

                    # Updated for pyJianYingDraft 0.2.x API (removed replace_path)
                    amat = Audio_material(path=str(voice_dest), material_name=vpath.name)
                    try:
                        script.add_material(amat)
                    except Exception:
                        pass
                    
                    # Start voice at opening_offset (3.0s), duration is full length of audio file (or up to video end)
                    # We use voice_duration_us calculated earlier which was total_us - opening_offset_us
                    # target_timerange.start determines placement on timeline
                    aseg = Audio_segment(amat, target_timerange=Timerange(opening_offset_us, voice_duration_us))
                    script.add_segment(aseg, track_name=voice_track)
                    print(f"Inserted voice audio '{vpath.name}' on track '{voice_track}' starting at {opening_offset_us/SEC}s")
                except Exception as e:
                    print(f"Warning: Failed to insert voice audio: {e}")
            else:
                print(f"Warning: Voice file not found: {vpath}")
        except Exception as e:
            print(f"Warning: Voice insert error: {e}")

    # Save back to JSON (in-place)
    script.save()

    # Deduplicate tracks/materials (template carryover cleanup)
    _dedupe_tracks_and_materials(draft_dir)
    # Enforce fixed scale on video segments
    _force_video_scale(draft_dir, float(args.scale))

    fade_target = args.fade_duration if args.fade_duration is not None else args.crossfade
    if not getattr(args, "disable_auto_fade", False):
        try:
            applied = apply_auto_fade_transitions(draft_dir, track_name, float(fade_target or 0.0))
            if applied == 0:
                logger.info("Auto-fade: no transitions added (track=%s, fade=%.2fs)", track_name, float(fade_target or 0.0))
        except Exception as exc:
            logger.warning(f"Auto-fade injection failed: {exc}")

    _merge_info_tracks_into_content(draft_dir)
    print(f"Inserted {len(cues)} images into draft: {args.new}\nLocation: {args.draft_root}/{args.new}")

    # ========================================
    # 🔧 CRITICAL FIX: Set render_timerange for image segments
    # ========================================
    # pyJianYingDraft doesn't set render_timerange correctly, causing images not to display
    logger.info("🖼️  Fixing render_timerange for image segments...")
    if fix_image_track_render_timerange(draft_dir):
        logger.info("✅ Image render_timerange fixed")
    else:
        logger.warning("⚠️  render_timerange fix failed - images may not display")

    # ========================================
    # 🔧 CRITICAL: Sync draft_info.json with draft_content.json
    # ========================================
    # pyJianYingDraft 0.2.3 only updates draft_content.json
    # CapCut requires BOTH files to be in sync to recognize tracks/materials
    logger.info("🔄 Syncing draft_info.json with draft_content.json...")
    if sync_draft_info_with_content(draft_dir):
        logger.info("✅ Draft files synchronized - CapCut will now recognize all changes")
    else:
        logger.warning("⚠️  Draft sync failed - manual sync may be required")
        logger.warning(f"   Run: python3 tools/sync_draft_files_complete.py '{draft_dir}'")

    # Ensure absolute_index is populated so CapCut shows inserted tracks
    ensure_absolute_indices(draft_dir)

    # ========================================
    # 🎯 帯ポストプロセッシング（チャンネル共通）
    #    CH01固定のスタイルは排除し、belt_config + layout_configのみで適用
    # ========================================
    if args.belt_config:
        try:
            import json as _json
            belt_config_path = Path(args.belt_config)
            if belt_config_path.exists():
                belt_data = _json.loads(belt_config_path.read_text(encoding='utf-8'))

                logger.info("🎯 帯ポストプロセッシング開始")

                # ステップ1: 帯レイヤーを上書き・最上位に移動
                logger.info("📝 帯レイヤーを上書き・最上位に移動...")
                layout_cfg = preset.config_model.layout if preset and preset.config_model else None
                apply_belt_config(
                    belt_data,
                    args.opening_offset,
                    draft_dir,
                    logger,
                    title=args.title,
                    layout_config=layout_cfg,
                    channel_id=preset.channel_id if preset else None,
                )

                # ステップ2: エフェクトのエンド位置調整
                logger.info("✨ エフェクト終了位置を調整...")
                total_duration_sec = belt_data.get('total_duration') or 0.0
                adjust_effect_duration(None, total_duration_sec, draft_dir, logger)

                # 再度同期（ポストプロセッシング後）
                logger.info("🔄 ポストプロセッシング後の同期...")
                # CH01など冒頭ブランクを確実に反映（テンプレ由来トラックをシフト）
                if opening_offset_us > 0:
                    _shift_tracks_in_json(draft_dir, opening_offset_us)
                
                # 字幕スタイルの強制修正 (Using Adapter now) ※チャンネル共通
                fix_subtitle_style_direct(str(draft_dir / "draft_info.json"), adapter)

                if sync_draft_info_with_content(draft_dir):
                    logger.info("✅ ドラフト作成完了 (帯/字幕反映)")
                else:
                    logger.warning("⚠️  同期失敗")

            else:
                logger.warning(f"Belt config file not found: {belt_config_path}")
        except Exception as e:
            logger.error(f"Belt layer processing failed: {e}")
            import traceback
            logger.debug(traceback.format_exc())

    # 最終チェック: 開始オフセット違反を検出（BGM・背景トラックのみ除外）
    _validate_opening_offset(draft_dir, opening_offset_us, logger)

    # 強制的にスケールを反映（pyJianYingDraftがリセットする場合のガード）
    _force_video_scale(draft_dir, float(args.scale))
    sync_draft_info_with_content(draft_dir)

    # Ensure draft_info has name/id for CapCut discoverability
    try:
        import uuid
        info_path = draft_dir / "draft_info.json"
        if info_path.exists():
            info_data = json.loads(info_path.read_text(encoding="utf-8"))
            if not info_data.get("draft_name"):
                info_data["draft_name"] = args.new
            if not info_data.get("draft_id"):
                info_data["draft_id"] = str(uuid.uuid4()).upper()
            info_path.write_text(json.dumps(info_data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        logger.warning("Could not update draft_info.json with name/id")

    # Update root_meta_info.json so CapCut UI can list the draft
    try:
        root_meta = Path(args.draft_root) / "root_meta_info.json"
        now_us = int(time.time() * 1_000_000)
        cover_path = draft_dir / "draft_cover.jpg"
        draft_id = info_data.get("draft_id") if "info_data" in locals() else None
        if not draft_id:
            import uuid
            draft_id = str(uuid.uuid4()).upper()
        entry = {
            "draft_name": args.new,
            "draft_id": draft_id,
            "draft_fold_path": str(draft_dir),
            "draft_json_file": str(draft_dir / "draft_info.json"),
            "draft_root_path": str(Path(args.draft_root)),
            "draft_cover": str(cover_path) if cover_path.exists() else "",
            "draft_is_invisible": False,
            "draft_is_cloud_temp_draft": False,
            "draft_is_ai_shorts": False,
            "draft_is_web_article_video": False,
            "draft_type": "",
            "streaming_edit_draft_ready": True,
            "tm_draft_create": now_us,
            "tm_draft_modified": now_us,
            "tm_duration": 0,
        }
        data = {"all_draft_store": []}
        if root_meta.exists():
            try:
                data = json.loads(root_meta.read_text(encoding="utf-8"))
            except Exception:
                logger.warning("root_meta_info.json parse failed; recreating minimal structure")
                data = {"all_draft_store": []}
        drafts = data.get("all_draft_store", [])
        drafts = [d for d in drafts if d.get("draft_fold_path") != str(draft_dir)]
        drafts.append(entry)
        data["all_draft_store"] = drafts
        root_meta.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        logger.warning("Could not update root_meta_info.json for CapCut draft listing")

    # --- Improve discoverability in output run dir ---
    try:
        import datetime, json as _json
        # Create a stable symlink from run_dir -> actual CapCut draft folder
        run_capcut_link = run_dir / 'capcut_draft'
        if run_capcut_link.exists() or run_capcut_link.is_symlink():
            try:
                if run_capcut_link.is_symlink() or run_capcut_link.is_file():
                    run_capcut_link.unlink()
                else:
                    shutil.rmtree(run_capcut_link)
            except Exception:
                pass
        run_capcut_link.symlink_to(draft_dir)

        # Also drop an info JSON for quick reference/search in output/
        info = {
            'draft_name': args.new,
            'draft_path': str(draft_dir),
            'created_at': datetime.datetime.now().isoformat(timespec='seconds'),
            'project_id': Path(args.run).resolve().name,
            'template_used': template_name,
            'transform': {'tx': args.tx, 'ty': args.ty, 'scale': args.scale},
            'crossfade_sec': crossfade,
            'fade_duration_sec': fade_target,
            'title': args.title,
            'srt_file': args.srt_file,
        }
        (run_dir / 'capcut_draft_info.json').write_text(_json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as e:
        print(f"Note: Could not create output symlink/info: {e}")


if __name__ == "__main__":
    main()

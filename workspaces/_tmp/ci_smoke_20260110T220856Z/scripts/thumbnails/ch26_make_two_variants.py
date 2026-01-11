#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CH26: Create two thumbnail variants per video from existing assets only.

Why:
  Some existing CH26 backgrounds (10_bg.png) already contain a generated portrait,
  and when we composite the real portrait (20_portrait.png) we get a "double face".

Output per video (workspaces/thumbnails/assets/CH26/NNN/):
  - 00_thumb_1.png  : RECOMMENDED (thumb_1). Real portrait overlay + text.
      - if 10_bg already has a portrait -> suppress the bg face to avoid double, then overlay.
      - else -> overlay as-is.
  - 00_thumb_2.png  : ALTERNATE (thumb_2).
      - if 10_bg already has a portrait -> bg as-is + text (no overlay).
      - else -> real portrait overlay + text (no suppression; more bg detail).
  - 00_thumb.png    : canonical file reference (always a copy of 00_thumb_1.png)

Notes:
  - Does NOT generate new images (no API calls).
  - Uses `workspaces/thumbnails/compiler/policies/ch26_portrait_overrides_v1.yaml` for per-video portrait tweaks.
  - By default it does NOT touch `projects.json` unless you opt-in.
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter


def _discover_repo_root(start: Path) -> Path:
    cur = start if start.is_dir() else start.parent
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate.resolve()
    raise RuntimeError("repo root not found (pyproject.toml). Run from inside the repo.")


try:
    from _bootstrap import bootstrap
except ModuleNotFoundError:
    repo_root = _discover_repo_root(Path(__file__).resolve())
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from _bootstrap import bootstrap

bootstrap()

from factory_common import paths as fpaths  # noqa: E402
from script_pipeline.thumbnails.compiler.layer_specs import resolve_channel_layer_spec_ids, load_layer_spec_yaml  # noqa: E402
from script_pipeline.thumbnails.compiler.layer_specs import find_text_layout_item_for_video  # noqa: E402
from script_pipeline.thumbnails.layers.image_layer import BgEnhanceParams, composited_portrait_path, enhanced_bg_path  # noqa: E402
from script_pipeline.thumbnails.layers.text_layer import compose_text_to_png  # noqa: E402
from script_pipeline.thumbnails.thumb_spec import extract_normalized_override_leaf, load_thumb_spec  # noqa: E402
from script_pipeline.thumbnails.tools.layer_specs_builder import upsert_fs_variant  # noqa: E402
from script_pipeline.tools import planning_store  # noqa: E402


PORTRAITLESS_BG_VIDEOS = {"001", "002", "003", "004"}


def _normalize_channel(channel: str) -> str:
    return str(channel or "").strip().upper()


def _normalize_video(video: str) -> str:
    raw = str(video or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        raise ValueError(f"invalid video: {video}")
    return digits.zfill(3)


def _atomic_copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    shutil.copyfile(src, tmp)
    tmp.replace(dest)


def _load_existing_project_status(*, channel: str, video: str) -> str:
    """
    Preserve manual UI statuses (e.g. approved) when optionally registering variants.
    """
    ch = _normalize_channel(channel)
    v = _normalize_video(video)
    projects_path = fpaths.thumbnails_root() / "projects.json"
    try:
        doc = json.loads(projects_path.read_text(encoding="utf-8"))
    except Exception:
        return "review"
    items = doc.get("projects") if isinstance(doc, dict) else None
    if not isinstance(items, list):
        return "review"
    for entry in items:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("channel") or "").upper() != ch:
            continue
        if _normalize_video(entry.get("video")) != v:
            continue
        st = str(entry.get("status") or "").strip()
        return st or "review"
    return "review"


def _load_compiler_bg_params(channel: str) -> BgEnhanceParams:
    ch = _normalize_channel(channel)
    templates_path = fpaths.thumbnails_root() / "templates.json"
    payload = json.loads(templates_path.read_text(encoding="utf-8"))
    channels = payload.get("channels") if isinstance(payload, dict) else None
    channel_doc = channels.get(ch) if isinstance(channels, dict) else None
    defaults = channel_doc.get("compiler_defaults") if isinstance(channel_doc, dict) else None
    bg = defaults.get("bg_enhance") if isinstance(defaults, dict) and isinstance(defaults.get("bg_enhance"), dict) else {}
    return BgEnhanceParams(
        brightness=float(bg.get("brightness") or 1.0),
        contrast=float(bg.get("contrast") or 1.0),
        color=float(bg.get("color") or 1.0),
        gamma=float(bg.get("gamma") or 1.0),
    )


def _load_ch26_portrait_policy() -> Dict[str, Any]:
    path = fpaths.thumbnails_root() / "compiler" / "policies" / "ch26_portrait_overrides_v1.yaml"
    if not path.exists():
        return {}
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return doc if isinstance(doc, dict) else {}


def _as_norm_box(value: Any, default: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return default
    out: List[float] = []
    for v in value:
        try:
            f = float(v)
        except Exception:
            return default
        if f < 0.0 or f > 1.0:
            return default
        out.append(f)
    return (out[0], out[1], out[2], out[3])


def _as_norm_offset(value: Any, default: Tuple[float, float]) -> Tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return default
    try:
        x = float(value[0])
        y = float(value[1])
    except Exception:
        return default
    return (x, y)


def _resolve_portrait_params(
    *,
    policy: Dict[str, Any],
    video: str,
    width: int,
    height: int,
) -> Dict[str, Any]:
    defaults = policy.get("defaults") if isinstance(policy.get("defaults"), dict) else {}
    overrides = policy.get("overrides") if isinstance(policy.get("overrides"), dict) else {}
    ov = overrides.get(video) if isinstance(overrides.get(video), dict) else {}

    dest_box_norm_default = _as_norm_box(defaults.get("dest_box"), (0.290, 0.060, 0.420, 0.760))
    dest_box_norm = _as_norm_box(ov.get("dest_box") if "dest_box" in ov else defaults.get("dest_box"), dest_box_norm_default)
    x, y, w, h = dest_box_norm
    dest_box_px = (int(round(width * x)), int(round(height * y)), int(round(width * w)), int(round(height * h)))

    anchor_default = str(defaults.get("anchor") or "bottom_center").strip() or "bottom_center"
    anchor = str(ov.get("anchor") if "anchor" in ov else anchor_default).strip() or anchor_default

    portrait_zoom_default = float(defaults.get("zoom") or 1.0)
    portrait_zoom = float(ov.get("zoom") if "zoom" in ov else portrait_zoom_default)

    offset_norm_default = _as_norm_offset(defaults.get("offset"), (0.0, 0.0))
    offset_norm = _as_norm_offset(ov.get("offset") if "offset" in ov else defaults.get("offset"), offset_norm_default)
    offset_px = (int(round(width * offset_norm[0])), int(round(height * offset_norm[1])))

    trim_transparent_default = bool(defaults.get("trim_transparent")) if "trim_transparent" in defaults else False
    trim_transparent = bool(ov.get("trim_transparent") if "trim_transparent" in ov else trim_transparent_default)

    fg_defaults = defaults.get("fg") if isinstance(defaults.get("fg"), dict) else {}
    fg_override = ov.get("fg") if isinstance(ov.get("fg"), dict) else {}
    fg_brightness = float(fg_override.get("brightness") if "brightness" in fg_override else (fg_defaults.get("brightness") or 1.20))
    fg_contrast = float(fg_override.get("contrast") if "contrast" in fg_override else (fg_defaults.get("contrast") or 1.08))
    fg_color = float(fg_override.get("color") if "color" in fg_override else (fg_defaults.get("color") or 0.98))

    return {
        "dest_box_px": dest_box_px,
        "anchor": anchor,
        "zoom": portrait_zoom,
        "offset_px": offset_px,
        "trim_transparent": trim_transparent,
        "fg_brightness": fg_brightness,
        "fg_contrast": fg_contrast,
        "fg_color": fg_color,
    }


@dataclass(frozen=True)
class PlanningCopy:
    top: str
    accent: str


def _load_planning_copy(channel: str, video: str) -> PlanningCopy:
    ch = _normalize_channel(channel)
    v = _normalize_video(video)
    for row in planning_store.get_rows(ch, force_refresh=True):
        try:
            row_v = _normalize_video(row.video_number or "")
        except Exception:
            continue
        if row_v != v:
            continue
        raw = row.raw if isinstance(row.raw, dict) else {}
        top = str(raw.get("サムネタイトル上") or "").strip()
        accent = str(raw.get("サムネタイトル下") or "").strip()
        return PlanningCopy(top=top, accent=accent)
    return PlanningCopy(top="", accent="")


def _soft_ellipse_mask(size: Tuple[int, int], *, box: Tuple[int, int, int, int], blur_px: int) -> Image.Image:
    w, h = size
    x0, y0, x1, y1 = box
    x0 = max(0, min(w, int(x0)))
    y0 = max(0, min(h, int(y0)))
    x1 = max(0, min(w, int(x1)))
    y1 = max(0, min(h, int(y1)))
    mask = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask)
    d.ellipse((x0, y0, x1, y1), fill=255)
    if blur_px > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(radius=int(blur_px)))
    return mask


def _suppress_center_person(
    base_path: Path,
    *,
    dest_box_px: Tuple[int, int, int, int],
    strength: str = "moderate",
) -> Path:
    """
    Build a temp background where the portrait area is blurred+darkened.

    This is a local, deterministic edit (no image generation) to prevent "double face"
    when the background already contains a portrait.
    """
    with Image.open(base_path) as im:
        img = im.convert("RGBA")
    w, h = img.size

    strength_key = str(strength or "moderate").strip().lower()
    hard = strength_key in {"hard", "double", "double_face", "aggressive"}

    if hard:
        # For backgrounds that already contain a portrait, suppress very aggressively so
        # we never end up with a visible second face.
        blur_radius = max(18, int(round(min(w, h) * 0.08)))
        suppressed = img.filter(ImageFilter.GaussianBlur(radius=blur_radius))
        suppressed_rgb = suppressed.convert("RGB")
        suppressed_rgb = ImageEnhance.Brightness(suppressed_rgb).enhance(0.14)
        suppressed_rgb = ImageEnhance.Contrast(suppressed_rgb).enhance(0.72)
        suppressed_rgb = ImageEnhance.Color(suppressed_rgb).enhance(0.82)
        suppressed = suppressed_rgb.convert("RGBA")
        suppressed = Image.alpha_composite(suppressed, Image.new("RGBA", suppressed.size, (0, 0, 0, 230)))
        out = suppressed
    else:
        # For portrait-less backgrounds, we only need a clean "spot" behind the overlay portrait.
        x0, y0, bw, bh = dest_box_px
        base_pad = int(round(min(w, h) * 0.06))
        pad_x = base_pad + int(round(bw * 0.12))
        pad_y = base_pad + int(round(bh * 0.08))
        raw_box = (x0 - pad_x, y0 - pad_y, x0 + bw + pad_x, y0 + bh + pad_y)

        x0c = max(0, min(w, int(raw_box[0])))
        y0c = max(0, min(h, int(raw_box[1])))
        x1c = max(0, min(w, int(raw_box[2])))
        y1c = max(0, min(h, int(raw_box[3])))
        if x1c <= x0c:
            x1c = min(w, x0c + 1)
        if y1c <= y0c:
            y1c = min(h, y0c + 1)
        box = (x0c, y0c, x1c, y1c)

        patch = img.crop(box)
        blur_radius = max(6, int(round(min(patch.size) * 0.08)))
        patch = patch.filter(ImageFilter.GaussianBlur(radius=blur_radius))
        patch_rgb = patch.convert("RGB")
        patch_rgb = ImageEnhance.Brightness(patch_rgb).enhance(0.38)
        patch_rgb = ImageEnhance.Contrast(patch_rgb).enhance(0.85)
        patch_rgb = ImageEnhance.Color(patch_rgb).enhance(0.90)
        patch = patch_rgb.convert("RGBA")
        patch = Image.alpha_composite(patch, Image.new("RGBA", patch.size, (0, 0, 0, 140)))

        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        overlay.paste(patch, (x0c, y0c))
        mask = _soft_ellipse_mask(img.size, box=box, blur_px=int(round(min(w, h) * 0.06)))
        out = Image.composite(overlay, img, mask)

    handle = tempfile.NamedTemporaryFile(prefix="ch26_bg_suppressed_", suffix=".png", delete=False)
    try:
        tmp_path = Path(handle.name)
    finally:
        handle.close()
    out.save(tmp_path, format="PNG", optimize=True)
    return tmp_path


def _atomic_compose_text(
    base_image_path: Path,
    *,
    text_layout_spec: Dict[str, Any],
    video_id: str,
    out_path: Path,
    text_override: Optional[Dict[str, str]],
    template_id_override: Optional[str] = None,
    effects_override: Optional[Dict[str, Any]] = None,
    overlays_override: Optional[Dict[str, Any]] = None,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    compose_text_to_png(
        base_image_path,
        text_layout_spec=text_layout_spec,
        video_id=video_id,
        out_path=tmp,
        text_override=text_override,
        template_id_override=template_id_override,
        effects_override=effects_override,
        overlays_override=overlays_override,
    )
    tmp.replace(out_path)


def _parse_videos_arg(videos: Optional[List[str]]) -> List[str]:
    if not videos:
        return []
    out: List[str] = []
    for raw in videos:
        if not raw:
            continue
        for part in str(raw).split(","):
            part = part.strip()
            if not part:
                continue
            out.append(_normalize_video(part))
    return sorted(set(out))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="CH26: make two thumbnail variants (existing assets only)")
    ap.add_argument("--channel", default="CH26", help="channel code (default: CH26)")
    ap.add_argument("--videos", action="append", help="target videos (e.g. --videos 001,002)")
    ap.add_argument("--overwrite", action="store_true", help="overwrite existing thumbs")
    ap.add_argument("--register", action="store_true", help="update projects.json variants (keeps existing status when present)")
    args = ap.parse_args(argv)

    ch = _normalize_channel(args.channel)
    if ch != "CH26":
        raise ValueError("this tool is CH26-specific (safety)")

    targets = _parse_videos_arg(args.videos)
    if not targets:
        targets = [f"{i:03d}" for i in range(1, 31)]

    _, txt_id = resolve_channel_layer_spec_ids(ch)
    if not txt_id:
        raise RuntimeError(f"text_layout_id not configured for channel={ch}")
    text_spec = load_layer_spec_yaml(txt_id)

    bg_params = _load_compiler_bg_params(ch)
    band_params = BgEnhanceParams()  # identity (we do not want bands; keep deterministic)
    portrait_policy = _load_ch26_portrait_policy()

    ok = 0
    skipped = 0
    failed = 0

    for vid in targets:
        v = _normalize_video(vid)
        video_dir = fpaths.thumbnail_assets_dir(ch, v)
        bg_path = video_dir / "10_bg.png"
        portrait_path = video_dir / "20_portrait.png"
        if not bg_path.exists():
            print(f"{ch}-{v}: missing 10_bg.png (skip)")
            skipped += 1
            continue
        if not portrait_path.exists():
            print(f"{ch}-{v}: missing 20_portrait.png (skip)")
            skipped += 1
            continue

        out_1 = video_dir / "00_thumb_1.png"
        out_2 = video_dir / "00_thumb_2.png"
        out_main = video_dir / "00_thumb.png"
        if not args.overwrite and out_1.exists() and out_2.exists() and out_main.exists():
            print(f"{ch}-{v}: skip (already exists)")
            skipped += 1
            continue

        try:
            copy = _load_planning_copy(ch, v)
            text_override = {"top": copy.top, "accent": copy.accent}
            video_id = f"{ch}-{v}"

            width, height = 1920, 1080
            thumb_spec = load_thumb_spec(ch, v)
            overrides_leaf = extract_normalized_override_leaf(thumb_spec.payload) if thumb_spec else {}

            portrait_params = _resolve_portrait_params(policy=portrait_policy, video=v, width=width, height=height)
            dest_box_px = portrait_params["dest_box_px"]
            anchor = portrait_params["anchor"]
            portrait_zoom = portrait_params["zoom"]
            portrait_offset_px = portrait_params["offset_px"]
            trim_transparent = portrait_params["trim_transparent"]
            fg_brightness = portrait_params["fg_brightness"]
            fg_contrast = portrait_params["fg_contrast"]
            fg_color = portrait_params["fg_color"]

            if overrides_leaf:
                if "overrides.portrait.zoom" in overrides_leaf:
                    portrait_zoom = float(overrides_leaf["overrides.portrait.zoom"])
                if "overrides.portrait.offset_x" in overrides_leaf:
                    portrait_offset_px = (int(round(width * float(overrides_leaf["overrides.portrait.offset_x"]))), portrait_offset_px[1])
                if "overrides.portrait.offset_y" in overrides_leaf:
                    portrait_offset_px = (portrait_offset_px[0], int(round(height * float(overrides_leaf["overrides.portrait.offset_y"]))))
                if "overrides.portrait.trim_transparent" in overrides_leaf:
                    trim_transparent = bool(overrides_leaf["overrides.portrait.trim_transparent"])
                if "overrides.portrait.fg_brightness" in overrides_leaf:
                    fg_brightness = float(overrides_leaf["overrides.portrait.fg_brightness"])
                if "overrides.portrait.fg_contrast" in overrides_leaf:
                    fg_contrast = float(overrides_leaf["overrides.portrait.fg_contrast"])
                if "overrides.portrait.fg_color" in overrides_leaf:
                    fg_color = float(overrides_leaf["overrides.portrait.fg_color"])

            has_bg_portrait = v not in PORTRAITLESS_BG_VIDEOS

            video_bg_params = BgEnhanceParams(
                brightness=float(overrides_leaf.get("overrides.bg_enhance.brightness", bg_params.brightness)),
                contrast=float(overrides_leaf.get("overrides.bg_enhance.contrast", bg_params.contrast)),
                color=float(overrides_leaf.get("overrides.bg_enhance.color", bg_params.color)),
                gamma=float(overrides_leaf.get("overrides.bg_enhance.gamma", bg_params.gamma)),
            )
            video_bg_zoom = float(overrides_leaf.get("overrides.bg_pan_zoom.zoom", 1.0))
            video_bg_pan_x = float(overrides_leaf.get("overrides.bg_pan_zoom.pan_x", 0.0))
            video_bg_pan_y = float(overrides_leaf.get("overrides.bg_pan_zoom.pan_y", 0.0))

            template_id_override = str(overrides_leaf.get("overrides.text_template_id") or "").strip() or None
            effects_override: Optional[Dict[str, Any]] = None
            overlays_override: Optional[Dict[str, Any]] = None
            if overrides_leaf:
                stroke: Dict[str, Any] = {}
                shadow: Dict[str, Any] = {}
                glow: Dict[str, Any] = {}
                fills: Dict[str, Any] = {}

                if "overrides.text_effects.stroke.width_px" in overrides_leaf:
                    stroke["width_px"] = overrides_leaf["overrides.text_effects.stroke.width_px"]
                if "overrides.text_effects.stroke.color" in overrides_leaf:
                    stroke["color"] = overrides_leaf["overrides.text_effects.stroke.color"]

                if "overrides.text_effects.shadow.alpha" in overrides_leaf:
                    shadow["alpha"] = overrides_leaf["overrides.text_effects.shadow.alpha"]
                if "overrides.text_effects.shadow.offset_px" in overrides_leaf:
                    off = overrides_leaf["overrides.text_effects.shadow.offset_px"]
                    if isinstance(off, tuple) and len(off) == 2:
                        shadow["offset_px"] = [int(off[0]), int(off[1])]
                    else:
                        shadow["offset_px"] = off
                if "overrides.text_effects.shadow.blur_px" in overrides_leaf:
                    shadow["blur_px"] = overrides_leaf["overrides.text_effects.shadow.blur_px"]
                if "overrides.text_effects.shadow.color" in overrides_leaf:
                    shadow["color"] = overrides_leaf["overrides.text_effects.shadow.color"]

                if "overrides.text_effects.glow.alpha" in overrides_leaf:
                    glow["alpha"] = overrides_leaf["overrides.text_effects.glow.alpha"]
                if "overrides.text_effects.glow.blur_px" in overrides_leaf:
                    glow["blur_px"] = overrides_leaf["overrides.text_effects.glow.blur_px"]
                if "overrides.text_effects.glow.color" in overrides_leaf:
                    glow["color"] = overrides_leaf["overrides.text_effects.glow.color"]

                for fill_key in ("white_fill", "red_fill", "yellow_fill", "hot_red_fill", "purple_fill"):
                    p = f"overrides.text_fills.{fill_key}.color"
                    if p in overrides_leaf:
                        fills[fill_key] = {"color": overrides_leaf[p]}

                eff: Dict[str, Any] = {}
                if stroke:
                    eff["stroke"] = stroke
                if shadow:
                    eff["shadow"] = shadow
                if glow:
                    eff["glow"] = glow
                if fills:
                    eff.update(fills)
                if eff:
                    effects_override = eff

                left_tsz: Dict[str, Any] = {}
                top_band: Dict[str, Any] = {}
                bottom_band: Dict[str, Any] = {}
                for k in ("enabled", "color", "alpha_left", "alpha_right", "x0", "x1"):
                    p = f"overrides.overlays.left_tsz.{k}"
                    if p in overrides_leaf:
                        left_tsz[k] = overrides_leaf[p]
                for k in ("enabled", "color", "alpha_top", "alpha_bottom", "y0", "y1"):
                    p = f"overrides.overlays.top_band.{k}"
                    if p in overrides_leaf:
                        top_band[k] = overrides_leaf[p]
                for k in ("enabled", "color", "alpha_top", "alpha_bottom", "y0", "y1"):
                    p = f"overrides.overlays.bottom_band.{k}"
                    if p in overrides_leaf:
                        bottom_band[k] = overrides_leaf[p]
                ov: Dict[str, Any] = {}
                if left_tsz:
                    ov["left_tsz"] = left_tsz
                if top_band:
                    ov["top_band"] = top_band
                if bottom_band:
                    ov["bottom_band"] = bottom_band
                if ov:
                    overlays_override = ov

            video_text_scale = float(overrides_leaf.get("overrides.text_scale", 1.0))
            template_id_for_scale: Optional[str] = None
            item = find_text_layout_item_for_video(text_spec, video_id)
            if isinstance(item, dict):
                template_id_for_scale = str(item.get("template_id") or "").strip() or None
            if template_id_override:
                template_id_for_scale = template_id_override

            text_spec_for_render = text_spec
            if abs(float(video_text_scale) - 1.0) > 1e-6 and isinstance(text_spec, dict):
                text_spec_for_render = copy.deepcopy(text_spec)
                templates_out = text_spec_for_render.get("templates") if isinstance(text_spec_for_render, dict) else None
                tpl_out = (
                    templates_out.get(template_id_for_scale)
                    if isinstance(templates_out, dict) and template_id_for_scale
                    else None
                )
                slots_out = tpl_out.get("slots") if isinstance(tpl_out, dict) else None
                if isinstance(slots_out, dict):
                    for slot_cfg in slots_out.values():
                        if not isinstance(slot_cfg, dict):
                            continue
                        base_size = slot_cfg.get("base_size_px")
                        if not isinstance(base_size, (int, float)):
                            continue
                        scaled = int(round(float(base_size) * float(video_text_scale)))
                        slot_cfg["base_size_px"] = max(1, scaled)

            with enhanced_bg_path(
                bg_path,
                params=video_bg_params,
                zoom=float(video_bg_zoom),
                pan_x=float(video_bg_pan_x),
                pan_y=float(video_bg_pan_y),
                band_params=band_params,
                band_x0=0.0,
                band_x1=0.0,
                band_power=1.0,
                temp_prefix=f"{video_id}_bg_",
            ) as base_for_text:
                if has_bg_portrait:
                    # thumb_1 (RECOMMENDED): suppress the background face and overlay the real portrait.
                    suppressed_path: Optional[Path] = None
                    try:
                        suppressed_path = _suppress_center_person(base_for_text, dest_box_px=dest_box_px, strength="hard")
                        with composited_portrait_path(
                            suppressed_path,
                            portrait_path=portrait_path,
                            dest_box_px=dest_box_px,
                            temp_prefix=f"{video_id}_base_",
                            anchor=anchor,
                            portrait_zoom=float(portrait_zoom),
                            portrait_offset_px=portrait_offset_px,
                            trim_transparent=bool(trim_transparent),
                            fg_brightness=fg_brightness,
                            fg_contrast=fg_contrast,
                            fg_color=fg_color,
                        ) as base_with_portrait:
                            _atomic_compose_text(
                                base_with_portrait,
                                text_layout_spec=text_spec_for_render,
                                video_id=video_id,
                                out_path=out_1,
                                text_override=text_override,
                                template_id_override=template_id_override,
                                effects_override=effects_override,
                                overlays_override=overlays_override,
                            )
                    finally:
                        if suppressed_path:
                            try:
                                suppressed_path.unlink()
                            except Exception:
                                pass

                    # thumb_2 (ALTERNATE): keep the background portrait as-is (no overlay).
                    _atomic_compose_text(
                        base_for_text,
                        text_layout_spec=text_spec_for_render,
                        video_id=video_id,
                        out_path=out_2,
                        text_override=text_override,
                        template_id_override=template_id_override,
                        effects_override=effects_override,
                        overlays_override=overlays_override,
                    )
                else:
                    # Background has no portrait (001-004). Ensure thumb_1 includes a person.
                    # thumb_1: suppress center slightly to create a clean "spot" behind the portrait.
                    suppressed_path: Optional[Path] = None
                    try:
                        suppressed_path = _suppress_center_person(base_for_text, dest_box_px=dest_box_px, strength="moderate")
                        with composited_portrait_path(
                            suppressed_path,
                            portrait_path=portrait_path,
                            dest_box_px=dest_box_px,
                            temp_prefix=f"{video_id}_base_",
                            anchor=anchor,
                            portrait_zoom=float(portrait_zoom),
                            portrait_offset_px=portrait_offset_px,
                            trim_transparent=bool(trim_transparent),
                            fg_brightness=fg_brightness,
                            fg_contrast=fg_contrast,
                            fg_color=fg_color,
                        ) as base_with_portrait:
                            _atomic_compose_text(
                                base_with_portrait,
                                text_layout_spec=text_spec_for_render,
                                video_id=video_id,
                                out_path=out_1,
                                text_override=text_override,
                                template_id_override=template_id_override,
                                effects_override=effects_override,
                                overlays_override=overlays_override,
                            )
                    finally:
                        if suppressed_path:
                            try:
                                suppressed_path.unlink()
                            except Exception:
                                pass

                    # thumb_2: same portrait, but without suppression (more background detail).
                    with composited_portrait_path(
                        base_for_text,
                        portrait_path=portrait_path,
                        dest_box_px=dest_box_px,
                        temp_prefix=f"{video_id}_base2_",
                        anchor=anchor,
                        portrait_zoom=float(portrait_zoom),
                        portrait_offset_px=portrait_offset_px,
                        trim_transparent=bool(trim_transparent),
                        fg_brightness=fg_brightness,
                        fg_contrast=fg_contrast,
                        fg_color=fg_color,
                    ) as base_with_portrait:
                        _atomic_compose_text(
                            base_with_portrait,
                            text_layout_spec=text_spec_for_render,
                            video_id=video_id,
                            out_path=out_2,
                            text_override=text_override,
                            template_id_override=template_id_override,
                            effects_override=effects_override,
                            overlays_override=overlays_override,
                        )

            # Keep the canonical direct-reference filename in sync with thumb_1.
            _atomic_copy(out_1, out_main)

            if args.register:
                status = _load_existing_project_status(channel=ch, video=v)
                rel_2 = f"{ch}/{v}/00_thumb_2.png"
                rel_1 = f"{ch}/{v}/00_thumb_1.png"
                upsert_fs_variant(channel=ch, video=v, title=None, image_rel_path=rel_2, label="thumb_2", status=status)
                upsert_fs_variant(channel=ch, video=v, title=None, image_rel_path=rel_1, label="thumb_1", status=status)

            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"{ch}-{v}: ERROR {exc}")
            failed += 1

    print(f"done: ok={ok} skipped={skipped} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

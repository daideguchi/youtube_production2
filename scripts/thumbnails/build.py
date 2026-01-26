#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unified Thumbnail Builder (build / retake / qc)

SSOT: `ssot/ops/OPS_THUMBNAILS_PIPELINE.md`
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

def _discover_repo_root(start: Path) -> Path:
    cur = start if start.is_dir() else start.parent
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate.resolve()
    raise RuntimeError("repo root not found (pyproject.toml). Run from inside the repo.")


try:
    from _bootstrap import bootstrap
except ModuleNotFoundError:
    repo_root: Optional[Path] = None
    for start in (Path.cwd().resolve(), Path(__file__).resolve()):
        try:
            repo_root = _discover_repo_root(start)
            break
        except Exception:
            continue
    if repo_root is None:
        raise
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from _bootstrap import bootstrap

bootstrap()

from factory_common import paths as fpaths  # noqa: E402
from script_pipeline.thumbnails.tools.buddha_3line_builder import (  # noqa: E402
    build_buddha_3line,
)
from script_pipeline.thumbnails.tools.layer_specs_builder import (  # noqa: E402
    build_channel_thumbnails,
    iter_targets_from_layer_specs,
)
from script_pipeline.thumbnails.io_utils import PngOutputMode, save_png_atomic  # noqa: E402

from PIL import Image, ImageDraw, ImageFont  # noqa: E402


SUPPORTED_PROJECT_STATUSES = {"draft", "in_progress", "review", "approved", "published", "archived"}


@dataclass(frozen=True)
class BgEnhance:
    brightness: float = 1.0
    contrast: float = 1.0
    color: float = 1.0
    gamma: float = 1.0


@dataclass(frozen=True)
class QcGrid:
    tile_w: int = 640
    tile_h: int = 360
    cols: int = 6
    pad: int = 8


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_channel(channel: str) -> str:
    return str(channel or "").strip().upper()


def _normalize_video(video: str) -> str:
    raw = str(video or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        raise ValueError(f"invalid video: {video}")
    return digits.zfill(3)


def _projects_path() -> Path:
    return fpaths.thumbnails_root() / "projects.json"


def _templates_path() -> Path:
    return fpaths.thumbnails_root() / "templates.json"


def _load_projects() -> Dict[str, Any]:
    path = _projects_path()
    return json.loads(path.read_text(encoding="utf-8"))


def _write_projects(doc: Dict[str, Any]) -> None:
    path = _projects_path()
    doc["version"] = int(doc.get("version") or 1)
    doc["updated_at"] = _now_iso()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _iter_channel_projects(doc: Dict[str, Any], channel: str) -> Iterable[Dict[str, Any]]:
    ch = _normalize_channel(channel)
    items = doc.get("projects")
    if not isinstance(items, list):
        return []
    return [p for p in items if isinstance(p, dict) and str(p.get("channel") or "").upper() == ch]


def _select_videos_by_status(channel: str, status: str) -> List[str]:
    st = str(status or "").strip()
    if st not in SUPPORTED_PROJECT_STATUSES:
        raise ValueError(f"unsupported status: {status}")
    doc = _load_projects()
    vids: List[str] = []
    for p in _iter_channel_projects(doc, channel):
        if str(p.get("status") or "").strip() == st:
            v = str(p.get("video") or "").strip()
            if v:
                vids.append(_normalize_video(v))
    return sorted(set(vids))


def _channel_has_layer_specs(channel: str) -> bool:
    ch = _normalize_channel(channel)
    payload = json.loads(_templates_path().read_text(encoding="utf-8"))
    channels = payload.get("channels") if isinstance(payload, dict) else None
    channel_doc = channels.get(ch) if isinstance(channels, dict) else None
    layer = channel_doc.get("layer_specs") if isinstance(channel_doc, dict) else None
    if not isinstance(layer, dict):
        return False
    img_id = layer.get("image_prompts_id")
    txt_id = layer.get("text_layout_id")
    return bool(isinstance(img_id, str) and img_id.strip() and isinstance(txt_id, str) and txt_id.strip())


def _load_compiler_defaults(channel: str) -> Dict[str, Any]:
    ch = _normalize_channel(channel)
    try:
        payload = json.loads(_templates_path().read_text(encoding="utf-8"))
    except Exception:
        return {}
    channels = payload.get("channels") if isinstance(payload, dict) else None
    channel_doc = channels.get(ch) if isinstance(channels, dict) else None
    defaults = channel_doc.get("compiler_defaults") if isinstance(channel_doc, dict) else None
    return defaults if isinstance(defaults, dict) else {}


def _apply_compiler_defaults(args: argparse.Namespace) -> None:
    defaults = _load_compiler_defaults(args.channel)
    bg = defaults.get("bg_enhance") if isinstance(defaults.get("bg_enhance"), dict) else {}
    qc = defaults.get("qc") if isinstance(defaults.get("qc"), dict) else {}

    def _set_float(name: str, key: str) -> None:
        cur = getattr(args, name, None)
        if cur is None:
            return
        if abs(float(cur) - 1.0) < 1e-9 and isinstance(bg.get(key), (int, float)):
            setattr(args, name, float(bg[key]))

    _set_float("bg_brightness", "brightness")
    _set_float("bg_contrast", "contrast")
    _set_float("bg_color", "color")
    _set_float("bg_gamma", "gamma")

    def _set_int(name: str, key: str) -> None:
        cur = getattr(args, name, None)
        if cur is None:
            return
        if isinstance(qc.get(key), int):
            setattr(args, name, int(qc[key]))

    # Only override QC defaults when the user did not provide anything custom.
    # (argparse gives us a number either way, so we key off the known default values)
    if getattr(args, "qc_tile_w", None) == 640 and getattr(args, "qc_tile_h", None) == 360 and getattr(args, "qc_cols", None) == 6 and getattr(args, "qc_pad", None) == 8:
        _set_int("qc_tile_w", "tile_w")
        _set_int("qc_tile_h", "tile_h")
        _set_int("qc_cols", "cols")
        _set_int("qc_pad", "pad")


def _detect_engine(channel: str, engine: str) -> str:
    e = str(engine or "auto").strip().lower()
    if e in {"auto", "layer_specs", "layer_specs_v3"}:
        if _channel_has_layer_specs(channel):
            return "layer_specs_v3"
        if e != "auto":
            raise RuntimeError(f"layer_specs not configured for channel={channel}")
        return "buddha_3line_v1"
    if e in {"buddha", "buddha_3line", "buddha_3line_v1"}:
        return "buddha_3line_v1"
    raise ValueError(f"unknown engine: {engine}")


def _resolve_thumb_style_preset(
    channel: str, style: str
) -> Tuple[Optional[str], Optional[str], Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, str]]]:
    """
    Return (text_template_id_override, image_prompts_id_override, effects_override_base, overlays_override_base, text_override_base)
    """
    ch = _normalize_channel(channel)
    key = str(style or "").strip().lower()
    if not key:
        return (None, None, None, None, None)

    # CH01: recent posts (parchment + black brush band + serif copy)
    if ch == "CH01" and key in {"ch01_recent_post_v1", "recent_post_v1", "recent_v1"}:
        overlays = {
            "left_tsz": {"enabled": False},
            "bottom_band": {
                "enabled": True,
                "mode": "brush",
                "color": "#000000",
                "alpha": 0.93,
                "y0": 0.44,
                "y1": 1.0,
                "roughness": 0.10,
                "feather_px": 28,
                "hole_count": 22,
                "blur_px": 2,
            },
        }
        return ("CH01_recent_post_bottom_band_3line_v1", "ch01_image_prompts_recent_post_v1", None, overlays, None)

    # CH01: gold / canva-like (strong right-stack copy + optional author)
    if ch == "CH01" and key in {"ch01_canva_gold_v1", "canva_gold_v1", "gold_v1", "ch01_gold_v1"}:
        overlays = {
            # Reuse the horizontal overlay system to darken the RIGHT text area.
            "left_tsz": {
                "enabled": True,
                "color": "#000000",
                "x0": 0.38,
                "x1": 1.0,
                "alpha_left": 0.10,
                "alpha_right": 0.65,
            }
        }
        text_override = {"author": "ブッダの教え"}
        return ("CH01_canva_gold_right_stack_3line_v1", None, None, overlays, text_override)

    raise SystemExit(f"unknown --thumb-style for channel={ch}: {style}")


def _default_buddha_base() -> Optional[Path]:
    p = fpaths.assets_root() / "thumbnails" / "CH12" / "ch12_buddha_bg_1536x1024.png"
    return p if p.exists() else None


def _parse_buddha_bases(raw: Optional[List[str]]) -> List[Path]:
    if not raw:
        return []
    out: List[Path] = []
    for item in raw:
        if not item:
            continue
        for part in str(item).split(","):
            p = part.strip()
            if not p:
                continue
            out.append(Path(p).expanduser())
    return out


def _group_videos_by_bucket_bases(*, videos: List[str], bases: List[Path], bucket_size: int) -> Dict[Path, List[str]]:
    if bucket_size <= 0:
        raise ValueError("bucket_size must be > 0")
    if not bases:
        raise ValueError("bases must be non-empty")

    groups: Dict[Path, List[str]] = {}
    for v in [_normalize_video(v) for v in videos]:
        idx = (int(v) - 1) // bucket_size
        base = bases[idx % len(bases)]
        groups.setdefault(base, []).append(v)
    return {k: sorted(vs) for k, vs in groups.items()}


def _append_fix_note(existing: Optional[str], line: str) -> str:
    suffix = line.strip()
    if not suffix:
        return (existing or "").strip()
    if not existing:
        return suffix
    # remove previous "修正済み:" lines to keep notes compact
    kept = [ln for ln in str(existing).splitlines() if not ln.startswith("修正済み:")]
    base = "\n".join(kept).rstrip()
    return (base + "\n" + suffix).strip()


def _mark_projects_done(
    *,
    channel: str,
    videos: List[str],
    status_done: str,
    fix_note_line: str,
) -> None:
    ch = _normalize_channel(channel)
    done = str(status_done or "review").strip()
    if done not in SUPPORTED_PROJECT_STATUSES:
        raise ValueError(f"unsupported status_done: {status_done}")
    now = _now_iso()

    doc = _load_projects()
    updated = 0
    for p in _iter_channel_projects(doc, ch):
        if _normalize_video(p.get("video")) not in set(videos):
            continue
        p["status"] = done
        p["status_updated_at"] = now
        p["updated_at"] = now
        p["notes"] = _append_fix_note(p.get("notes"), fix_note_line)
        updated += 1
    if updated:
        _write_projects(doc)


def _pick_font() -> ImageFont.ImageFont:
    for cand in ("/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"):
        try:
            return ImageFont.truetype(cand, 28)
        except Exception:
            continue
    return ImageFont.load_default()


def build_contactsheet(
    *,
    channel: str,
    videos: List[str],
    out_path: Path,
    grid: QcGrid,
    source_name: str = "00_thumb.png",
    output_mode: PngOutputMode = "final",
    allow_missing: bool = True,
) -> Path:
    ch = _normalize_channel(channel)
    vids = [_normalize_video(v) for v in videos]
    out_path.parent.mkdir(parents=True, exist_ok=True)

    selected_sources: Optional[Dict[str, Path]] = None
    source_key = str(source_name or "").strip()
    if source_key.lower() in {"selected", "selected_variant"}:
        doc = _load_projects()
        assets_root = fpaths.thumbnails_root() / "assets"
        selected_sources = {}
        for project in _iter_channel_projects(doc, ch):
            try:
                vid = _normalize_video(project.get("video") or "")
            except Exception:
                continue
            variants = project.get("variants")
            if not isinstance(variants, list):
                continue
            selected_id = str(project.get("selected_variant_id") or "").strip()
            chosen: Optional[Dict[str, Any]] = None
            if selected_id:
                for variant in variants:
                    if not isinstance(variant, dict):
                        continue
                    if str(variant.get("id") or "").strip() == selected_id:
                        chosen = variant
                        break
            if chosen is None:
                for variant in variants:
                    if isinstance(variant, dict):
                        chosen = variant
                        break
            if not chosen:
                continue
            image_rel = chosen.get("image_path")
            if not isinstance(image_rel, str) or not image_rel.strip():
                continue
            rel_path = Path(image_rel.strip())
            selected_sources[vid] = rel_path if rel_path.is_absolute() else (assets_root / rel_path)

    tile_w, tile_h = int(grid.tile_w), int(grid.tile_h)
    cols = max(1, int(grid.cols))
    rows = (len(vids) + cols - 1) // cols
    pad = max(0, int(grid.pad))

    W = cols * tile_w + (cols + 1) * pad
    H = rows * tile_h + (rows + 1) * pad
    canvas = Image.new("RGB", (W, H), (0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    font = _pick_font()

    missing_tiles: list[tuple[str, Optional[Path]]] = []

    for i, vid in enumerate(vids):
        r = i // cols
        c = i % cols
        x = pad + c * (tile_w + pad)
        y = pad + r * (tile_h + pad)
        src = selected_sources.get(vid) if selected_sources is not None else (fpaths.thumbnail_assets_dir(ch, vid) / source_name)
        if not src or not src.exists():
            if not allow_missing:
                missing_tiles.append((vid, src))
                continue
            tile = Image.new("RGB", (tile_w, tile_h), (30, 30, 30))
            canvas.paste(tile, (x, y))
            draw.text((x + 10, y + 10), f"NEEDS_RESTORE {vid}", fill=(255, 80, 80), font=font)
            continue
        with Image.open(src) as im:
            im = im.convert("RGB").resize((tile_w, tile_h), Image.LANCZOS)
            canvas.paste(im, (x, y))

        label = f"{vid}"
        bbox = draw.textbbox((0, 0), label, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        # Place the video label away from the typical "top-left title" area to avoid
        # covering the thumbnail's own top text.
        lx, ly = x + tile_w - 10 - tw, y + 10
        draw.rectangle((lx - 6, ly - 4, lx + tw + 6, ly + th + 4), fill=(0, 0, 0))
        draw.text((lx, ly), label, fill=(255, 255, 255), font=font)

    if missing_tiles and not allow_missing:
        details = ", ".join([f"{ch}-{vid}" for vid, _ in missing_tiles[:30]])
        suffix = "" if len(missing_tiles) <= 30 else f" (+{len(missing_tiles) - 30})"
        raise SystemExit(
            f"QC aborted: {len(missing_tiles)} thumbnails need restore before contactsheet can be generated: {details}{suffix}"
        )

    save_png_atomic(canvas, out_path, mode=output_mode, verify=True)
    return out_path


def _publish_qc_to_library(*, channel: str, qc_path: Path) -> Optional[Path]:
    """
    Publish a QC contactsheet into the per-channel thumbnail library so it can be
    viewed easily from the UI (Thumbnails > QC tab).
    """
    ch = _normalize_channel(channel)
    if not qc_path.exists():
        return None
    dest_dir = fpaths.thumbnails_root() / "assets" / ch / "library" / "qc"
    dest = dest_dir / "contactsheet.png"
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        shutil.copyfile(qc_path, tmp)
        tmp.replace(dest)
        print(f"[QC] published {dest}")
        return dest
    except Exception as e:
        print(f"[QC] WARN: failed to publish to library: {e}", file=sys.stderr)
        return None


def _cmd_build(args: argparse.Namespace) -> int:
    _apply_compiler_defaults(args)
    channel = _normalize_channel(args.channel)
    engine = _detect_engine(channel, args.engine)

    videos = [v for v in (args.videos or [])]
    if args.status:
        videos = _select_videos_by_status(channel, args.status)
    if engine == "layer_specs_v3":
        targets = iter_targets_from_layer_specs(channel, videos or None)
        (
            text_template_id_override,
            preset_image_prompts_id_override,
            effects_override_base,
            overlays_override_base,
            text_override_base,
        ) = _resolve_thumb_style_preset(
            channel, str(getattr(args, "thumb_style", "") or "")
        )
        cli_image_prompts_id_override = str(getattr(args, "image_prompts_id", "") or "").strip() or None
        resolved_image_prompts_id_override = cli_image_prompts_id_override or preset_image_prompts_id_override
        build_channel_thumbnails(
            channel=channel,
            targets=targets,
            width=int(args.width),
            height=int(args.height),
            stable_thumb_name=str(getattr(args, "thumb_name", "00_thumb.png") or "00_thumb.png"),
            variant_label=str(getattr(args, "variant_label", "") or "").strip() or None,
            update_projects=not bool(getattr(args, "no_update_projects", False)),
            force=bool(args.force),
            skip_generate=bool(args.skip_generate),
            regen_bg=bool(getattr(args, "regen_bg", False)),
            build_id=args.build_id,
            output_mode=args.output_mode,
            continue_on_error=bool(args.continue_on_error),
            max_gen_attempts=int(args.max_gen_attempts),
            export_flat=bool(args.export_flat),
            flat_name_suffix=str(args.flat_name_suffix),
            sleep_sec=float(args.sleep_sec),
            bg_brightness=float(args.bg_brightness),
            bg_contrast=float(args.bg_contrast),
            bg_color=float(args.bg_color),
            bg_gamma=float(args.bg_gamma),
            bg_zoom=float(args.bg_zoom),
            bg_pan_x=float(args.bg_pan_x),
            bg_pan_y=float(args.bg_pan_y),
            bg_band_brightness=float(args.bg_band_brightness),
            bg_band_contrast=float(args.bg_band_contrast),
            bg_band_color=float(args.bg_band_color),
            bg_band_gamma=float(args.bg_band_gamma),
            bg_band_x0=float(args.bg_band_x0),
            bg_band_x1=float(args.bg_band_x1),
            bg_band_power=float(args.bg_band_power),
            text_layout_id_override=str(getattr(args, "text_layout_id", "") or "").strip() or None,
            image_prompts_id_override=resolved_image_prompts_id_override,
            text_template_id_override=text_template_id_override,
            effects_override_base=effects_override_base,
            overlays_override_base=overlays_override_base,
            text_override_base=text_override_base,
        )
        built_videos = [t.video for t in targets]
        if args.qc:
            out = fpaths.thumbnails_root() / "assets" / channel / "_qc" / args.qc
            grid = QcGrid(tile_w=args.qc_tile_w, tile_h=args.qc_tile_h, cols=args.qc_cols, pad=args.qc_pad)
            build_contactsheet(
                channel=channel,
                videos=built_videos,
                out_path=out,
                grid=grid,
                source_name=str(getattr(args, "thumb_name", "00_thumb.png") or "00_thumb.png"),
                output_mode=args.output_mode,
            )
            print(f"[QC] wrote {out}")
            _publish_qc_to_library(channel=channel, qc_path=out)
        return 0

    # buddha_3line_v1
    if not videos:
        raise SystemExit("buddha_3line requires --videos (or --status)")
    bases = _parse_buddha_bases(getattr(args, "bases", None))
    if not bases:
        base = Path(args.base).expanduser() if args.base else (_default_buddha_base() or None)
        if not base:
            raise SystemExit("buddha_3line requires --base (or install default base under asset/thumbnails/CH12/)")
        bases = [base]

    missing = [str(p) for p in bases if not p.exists()]
    if missing:
        raise SystemExit(f"buddha_3line base image not found: {', '.join(missing)}")

    build_id = args.build_id or datetime.now(timezone.utc).strftime("build_%Y%m%dT%H%M%SZ")
    if len(bases) == 1:
        build_buddha_3line(
            channel=channel,
            videos=videos,
            base_image_path=bases[0],
            build_id=build_id,
            output_mode=args.output_mode,
            font_path=args.font_path,
            flip_base=not args.no_flip_base,
            impact=not args.no_impact,
            belt_override=True if args.belt else (False if args.no_belt else None),
            select_variant=bool(args.select_variant),
        )
    else:
        bucket_size = int(getattr(args, "base_bucket_size", 0) or 0)
        if bucket_size <= 0:
            if channel == "CH12":
                bucket_size = 10
            else:
                raise SystemExit("--base-bucket-size is required when using --bases (e.g. 10)")
        groups = _group_videos_by_bucket_bases(videos=videos, bases=bases, bucket_size=bucket_size)
        for base, vids in groups.items():
                build_buddha_3line(
                    channel=channel,
                    videos=vids,
                    base_image_path=base,
                    build_id=build_id,
                    output_mode=args.output_mode,
                    font_path=args.font_path,
                    flip_base=not args.no_flip_base,
                    impact=not args.no_impact,
                    belt_override=True if args.belt else (False if args.no_belt else None),
                    select_variant=bool(args.select_variant),
                )
    if args.qc:
        # For buddha builds, contactsheet sources are under compiler/<build_id>/out_01.png
        out = fpaths.thumbnails_root() / "assets" / channel / "_qc" / args.qc
        grid = QcGrid(tile_w=args.qc_tile_w, tile_h=args.qc_tile_h, cols=args.qc_cols, pad=args.qc_pad)
        build_contactsheet(
            channel=channel,
            videos=videos,
            out_path=out,
            grid=grid,
            source_name=f"compiler/{build_id}/out_01.png",
            output_mode=args.output_mode,
        )
        print(f"[QC] wrote {out}")
        _publish_qc_to_library(channel=channel, qc_path=out)
    return 0


def _cmd_retake(args: argparse.Namespace) -> int:
    _apply_compiler_defaults(args)
    channel = _normalize_channel(args.channel)
    engine = _detect_engine(channel, args.engine)
    videos = _select_videos_by_status(channel, args.status_from)
    if not videos:
        print(f"[retake] no targets for {channel} status={args.status_from}")
        return 0

    qc_name = args.qc or f"contactsheet_retake_{len(videos)}_{args.qc_tile_w}x{args.qc_tile_h}.png"
    has_pan_zoom = not (
        abs(float(args.bg_zoom) - 1.0) < 1e-9
        and abs(float(args.bg_pan_x)) < 1e-9
        and abs(float(args.bg_pan_y)) < 1e-9
    )
    pan_zoom_note = ""
    if has_pan_zoom:
        pan_zoom_note = f" pan(z={args.bg_zoom:.2f} px={args.bg_pan_x:.2f} py={args.bg_pan_y:.2f})"
    has_band = not (
        abs(float(args.bg_band_brightness) - 1.0) < 1e-9
        and abs(float(args.bg_band_contrast) - 1.0) < 1e-9
        and abs(float(args.bg_band_color) - 1.0) < 1e-9
        and abs(float(args.bg_band_gamma) - 1.0) < 1e-9
    )
    band_note = ""
    if has_band:
        band_note = (
            f" band(x={args.bg_band_x0:.2f}-{args.bg_band_x1:.2f} p={args.bg_band_power:.2f}"
            f" b={args.bg_band_brightness:.2f} c={args.bg_band_contrast:.2f}"
            f" s={args.bg_band_color:.2f} g={args.bg_band_gamma:.2f})"
        )
    built_note = (
        f"修正済み: engine={engine} bg(b={args.bg_brightness:.2f} c={args.bg_contrast:.2f}"
        f" s={args.bg_color:.2f} g={args.bg_gamma:.2f}){pan_zoom_note}{band_note} / QC={qc_name} / {_now_iso()}"
    )

    # Build (force) then mark review
    ns = argparse.Namespace(**vars(args))
    ns.videos = videos
    ns.status = None
    ns.force = True
    ns.qc = qc_name
    ns.qc_tile_w = args.qc_tile_w
    ns.qc_tile_h = args.qc_tile_h
    ns.qc_cols = args.qc_cols
    ns.qc_pad = args.qc_pad
    _cmd_build(ns)

    _mark_projects_done(channel=channel, videos=videos, status_done=args.status_done, fix_note_line=built_note)
    print(f"[retake] marked {len(videos)} projects -> {args.status_done}")
    return 0


def _cmd_qc(args: argparse.Namespace) -> int:
    channel = _normalize_channel(args.channel)
    videos = [v for v in (args.videos or [])]
    if args.status:
        videos = _select_videos_by_status(channel, args.status)
    if not videos:
        raise SystemExit("qc requires --videos or --status")

    out = Path(args.out).expanduser() if args.out else (fpaths.thumbnails_root() / "assets" / channel / "_qc" / "contactsheet.png")
    grid = QcGrid(tile_w=args.tile_w, tile_h=args.tile_h, cols=args.cols, pad=args.pad)
    build_contactsheet(
        channel=channel,
        videos=videos,
        out_path=out,
        grid=grid,
        source_name=args.source_name,
        output_mode=args.output_mode,
        allow_missing=bool(getattr(args, "allow_missing", False)),
    )
    print(f"[QC] wrote {out}")
    if args.out is None:
        _publish_qc_to_library(channel=channel, qc_path=out)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Thumbnail compiler: build/retake/qc")
    sub = ap.add_subparsers(dest="cmd", required=True)

    # build
    b = sub.add_parser("build", help="Build thumbnails (batch)")
    b.add_argument("--channel", required=True)
    b.add_argument("--engine", default="auto", help="auto|layer_specs|buddha_3line")
    b.add_argument("--videos", nargs="*")
    b.add_argument("--status", help="Build videos selected by projects.json status (e.g. in_progress)")
    b.add_argument("--qc", help="Write contactsheet under assets/{CH}/_qc/<name>.png")
    b.add_argument("--qc-tile-w", type=int, default=640)
    b.add_argument("--qc-tile-h", type=int, default=360)
    b.add_argument("--qc-cols", type=int, default=6)
    b.add_argument("--qc-pad", type=int, default=8)
    b.add_argument("--output-mode", choices=["draft", "final"], default="draft", help="PNG output mode (draft is faster)")

    # layer_specs args (ignored by buddha engine)
    b.add_argument("--width", type=int, default=1920)
    b.add_argument("--height", type=int, default=1080)
    b.add_argument("--text-layout-id", default="", help="Override layer_specs text_layout_id (registry id)")
    b.add_argument("--image-prompts-id", default="", help="Override layer_specs image_prompts_id (registry id)")
    b.add_argument(
        "--thumb-name",
        default="00_thumb.png",
        help="Output filename under assets/{CH}/{NNN}/ (default: 00_thumb.png; for A/B use 00_thumb_1.png, 00_thumb_2.png)",
    )
    b.add_argument(
        "--variant-label",
        default="",
        help="projects.json label override (default: auto; 00_thumb.png => thumb_00, otherwise stem of --thumb-name)",
    )
    b.add_argument(
        "--thumb-style",
        default="",
        help="Optional style preset (e.g. ch01_recent_post_v1). Applies text template + overlays without writing thumb_spec.",
    )
    b.add_argument("--force", action="store_true")
    b.add_argument(
        "--no-update-projects",
        dest="no_update_projects",
        action="store_true",
        help="Do not write to projects.json (safe for incident restore / image-only rebuilds).",
    )
    b.add_argument("--skip-generate", action="store_true")
    b.add_argument("--regen-bg", action="store_true", help="Regenerate background even if assets already exist (overwrites 90_bg_ai_raw/10_bg)")
    b.add_argument("--continue-on-error", action="store_true")
    b.add_argument("--max-gen-attempts", type=int, default=2)
    b.add_argument("--export-flat", action="store_true")
    b.add_argument("--flat-name-suffix", default="thumb")
    b.add_argument("--sleep-sec", type=float, default=0.25)
    b.add_argument("--bg-brightness", type=float, default=1.0)
    b.add_argument("--bg-contrast", type=float, default=1.0)
    b.add_argument("--bg-color", type=float, default=1.0)
    b.add_argument("--bg-gamma", type=float, default=1.0)
    b.add_argument("--bg-zoom", type=float, default=1.0)
    b.add_argument("--bg-pan-x", type=float, default=0.0)
    b.add_argument("--bg-pan-y", type=float, default=0.0)
    b.add_argument("--bg-band-brightness", type=float, default=1.0)
    b.add_argument("--bg-band-contrast", type=float, default=1.0)
    b.add_argument("--bg-band-color", type=float, default=1.0)
    b.add_argument("--bg-band-gamma", type=float, default=1.0)
    b.add_argument("--bg-band-x0", type=float, default=0.0)
    b.add_argument("--bg-band-x1", type=float, default=0.0)
    b.add_argument("--bg-band-power", type=float, default=1.0)

    # buddha args (ignored by layer_specs engine)
    b.add_argument("--base", help="Base background image path for buddha_3line")
    b.add_argument(
        "--bases",
        nargs="*",
        help="Multiple base backgrounds for buddha_3line (split by --base-bucket-size, default bucket=10 for CH12)",
    )
    b.add_argument(
        "--base-bucket-size",
        type=int,
        default=0,
        help="Bucket size when using --bases (e.g. 10 => 001-010 use base[0], 011-020 base[1], ...)",
    )
    b.add_argument("--build-id", help="compiler build_id (default: timestamp)")
    b.add_argument("--font-path", help="Optional font path override (TTF/OTF/TTC)")
    b.add_argument("--no-flip-base", action="store_true")
    b.add_argument("--no-impact", action="store_true")
    belt_group = b.add_mutually_exclusive_group()
    belt_group.add_argument("--belt", action="store_true")
    belt_group.add_argument("--no-belt", action="store_true")
    b.add_argument("--no-select-variant", action="store_false", dest="select_variant", default=True)

    # retake
    r = sub.add_parser("retake", help="Rebuild projects in status=in_progress and mark done")
    r.add_argument("--channel", required=True)
    r.add_argument("--engine", default="auto", help="auto|layer_specs|buddha_3line")
    r.add_argument("--status-from", default="in_progress", help="source status (default: in_progress)")
    r.add_argument("--status-done", default="review", help="done status (default: review)")
    r.add_argument("--qc", help="qc filename under assets/{CH}/_qc (default: auto)")
    r.add_argument("--qc-tile-w", type=int, default=640)
    r.add_argument("--qc-tile-h", type=int, default=360)
    r.add_argument("--qc-cols", type=int, default=6)
    r.add_argument("--qc-pad", type=int, default=8)
    r.add_argument("--output-mode", choices=["draft", "final"], default="draft", help="PNG output mode (draft is faster)")
    r.add_argument("--width", type=int, default=1920)
    r.add_argument("--height", type=int, default=1080)
    r.add_argument("--text-layout-id", default="", help="Override layer_specs text_layout_id (registry id)")
    r.add_argument("--image-prompts-id", default="", help="Override layer_specs image_prompts_id (registry id)")
    r.add_argument("--skip-generate", action="store_true")
    r.add_argument("--regen-bg", action="store_true", help="Regenerate background even if assets already exist (overwrites 90_bg_ai_raw/10_bg)")
    r.add_argument("--continue-on-error", action="store_true")
    r.add_argument("--max-gen-attempts", type=int, default=2)
    r.add_argument("--no-export-flat", action="store_false", dest="export_flat", default=True)
    r.add_argument("--flat-name-suffix", default="thumb")
    r.add_argument("--sleep-sec", type=float, default=0.25)
    r.add_argument("--bg-brightness", type=float, default=1.0)
    r.add_argument("--bg-contrast", type=float, default=1.0)
    r.add_argument("--bg-color", type=float, default=1.0)
    r.add_argument("--bg-gamma", type=float, default=1.0)
    r.add_argument("--bg-zoom", type=float, default=1.0)
    r.add_argument("--bg-pan-x", type=float, default=0.0)
    r.add_argument("--bg-pan-y", type=float, default=0.0)
    r.add_argument("--bg-band-brightness", type=float, default=1.0)
    r.add_argument("--bg-band-contrast", type=float, default=1.0)
    r.add_argument("--bg-band-color", type=float, default=1.0)
    r.add_argument("--bg-band-gamma", type=float, default=1.0)
    r.add_argument("--bg-band-x0", type=float, default=0.0)
    r.add_argument("--bg-band-x1", type=float, default=0.0)
    r.add_argument("--bg-band-power", type=float, default=1.0)
    r.add_argument("--base", help="Base background image path for buddha_3line (if needed)")
    r.add_argument(
        "--bases",
        nargs="*",
        help="Multiple base backgrounds for buddha_3line (split by --base-bucket-size, default bucket=10 for CH12)",
    )
    r.add_argument(
        "--base-bucket-size",
        type=int,
        default=0,
        help="Bucket size when using --bases (e.g. 10 => 001-010 use base[0], 011-020 base[1], ...)",
    )
    r.add_argument("--build-id", help="compiler build_id (default: timestamp)")
    r.add_argument("--font-path", help="Optional font path override (TTF/OTF/TTC)")
    r.add_argument("--no-flip-base", action="store_true")
    r.add_argument("--no-impact", action="store_true")
    r.add_argument("--belt", action="store_true")
    r.add_argument("--no-belt", action="store_true")
    r.add_argument("--no-select-variant", action="store_false", dest="select_variant", default=True)

    # qc
    q = sub.add_parser("qc", help="Generate contactsheet for a set of videos")
    q.add_argument("--channel", required=True)
    q.add_argument("--videos", nargs="*")
    q.add_argument("--status", help="Select videos by projects.json status")
    q.add_argument("--out", help="Output path (default: assets/{CH}/_qc/contactsheet.png)")
    q.add_argument(
        "--source-name",
        default="selected",
        help="Source image under assets/{CH}/{NNN}/. Use 'selected' to use projects.json selected_variant image_path (default: selected)",
    )
    q.add_argument("--allow-missing", action="store_true", help="Allow QC generation even when some assets are not restored")
    q.add_argument("--tile-w", type=int, default=640)
    q.add_argument("--tile-h", type=int, default=360)
    q.add_argument("--cols", type=int, default=6)
    q.add_argument("--pad", type=int, default=8)
    q.add_argument("--output-mode", choices=["draft", "final"], default="draft", help="PNG output mode (draft is faster)")

    args = ap.parse_args(argv)
    if args.cmd == "build":
        return _cmd_build(args)
    if args.cmd == "retake":
        return _cmd_retake(args)
    if args.cmd == "qc":
        return _cmd_qc(args)
    raise SystemExit(f"unknown cmd: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())

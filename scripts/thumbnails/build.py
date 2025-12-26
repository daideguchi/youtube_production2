#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unified Thumbnail Builder (build / retake / qc)

SSOT: `ssot/ops/OPS_THUMBNAILS_PIPELINE.md`
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

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


def _default_buddha_base() -> Optional[Path]:
    p = fpaths.assets_root() / "thumbnails" / "CH12" / "ch12_buddha_bg_1536x1024.png"
    return p if p.exists() else None


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
) -> Path:
    ch = _normalize_channel(channel)
    vids = [_normalize_video(v) for v in videos]
    out_path.parent.mkdir(parents=True, exist_ok=True)

    tile_w, tile_h = int(grid.tile_w), int(grid.tile_h)
    cols = max(1, int(grid.cols))
    rows = (len(vids) + cols - 1) // cols
    pad = max(0, int(grid.pad))

    W = cols * tile_w + (cols + 1) * pad
    H = rows * tile_h + (rows + 1) * pad
    canvas = Image.new("RGB", (W, H), (0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    font = _pick_font()

    for i, vid in enumerate(vids):
        r = i // cols
        c = i % cols
        x = pad + c * (tile_w + pad)
        y = pad + r * (tile_h + pad)
        src = fpaths.thumbnail_assets_dir(ch, vid) / source_name
        if not src.exists():
            tile = Image.new("RGB", (tile_w, tile_h), (30, 30, 30))
            canvas.paste(tile, (x, y))
            draw.text((x + 10, y + 10), f"MISSING {vid}", fill=(255, 80, 80), font=font)
            continue
        with Image.open(src) as im:
            im = im.convert("RGB").resize((tile_w, tile_h), Image.LANCZOS)
            canvas.paste(im, (x, y))

        label = f"{vid}"
        bbox = draw.textbbox((0, 0), label, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        lx, ly = x + 10, y + 10
        draw.rectangle((lx - 6, ly - 4, lx + tw + 6, ly + th + 4), fill=(0, 0, 0))
        draw.text((lx, ly), label, fill=(255, 255, 255), font=font)

    canvas.save(out_path, format="PNG", optimize=True)
    return out_path


def _cmd_build(args: argparse.Namespace) -> int:
    _apply_compiler_defaults(args)
    channel = _normalize_channel(args.channel)
    engine = _detect_engine(channel, args.engine)

    videos = [v for v in (args.videos or [])]
    if args.status:
        videos = _select_videos_by_status(channel, args.status)
    if engine == "layer_specs_v3":
        targets = iter_targets_from_layer_specs(channel, videos or None)
        build_channel_thumbnails(
            channel=channel,
            targets=targets,
            width=int(args.width),
            height=int(args.height),
            force=bool(args.force),
            skip_generate=bool(args.skip_generate),
            continue_on_error=bool(args.continue_on_error),
            max_gen_attempts=int(args.max_gen_attempts),
            export_flat=bool(args.export_flat),
            flat_name_suffix=str(args.flat_name_suffix),
            sleep_sec=float(args.sleep_sec),
            bg_brightness=float(args.bg_brightness),
            bg_contrast=float(args.bg_contrast),
            bg_color=float(args.bg_color),
            bg_gamma=float(args.bg_gamma),
        )
        built_videos = [t.video for t in targets]
        if args.qc:
            out = fpaths.thumbnails_root() / "assets" / channel / "_qc" / args.qc
            grid = QcGrid(tile_w=args.qc_tile_w, tile_h=args.qc_tile_h, cols=args.qc_cols, pad=args.qc_pad)
            build_contactsheet(channel=channel, videos=built_videos, out_path=out, grid=grid)
            print(f"[QC] wrote {out}")
        return 0

    # buddha_3line_v1
    if not videos:
        raise SystemExit("buddha_3line requires --videos (or --status)")
    base = Path(args.base).expanduser() if args.base else (_default_buddha_base() or None)
    if not base:
        raise SystemExit("buddha_3line requires --base (or install default base under asset/thumbnails/CH12/)")
    build_id = args.build_id or datetime.now(timezone.utc).strftime("build_%Y%m%dT%H%M%SZ")
    build_buddha_3line(
        channel=channel,
        videos=videos,
        base_image_path=base,
        build_id=build_id,
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
        )
        print(f"[QC] wrote {out}")
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
    built_note = f"修正済み: engine={engine} bg(b={args.bg_brightness:.2f} c={args.bg_contrast:.2f} s={args.bg_color:.2f} g={args.bg_gamma:.2f}) / QC={qc_name} / {_now_iso()}"

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
    build_contactsheet(channel=channel, videos=videos, out_path=out, grid=grid, source_name=args.source_name)
    print(f"[QC] wrote {out}")
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

    # layer_specs args (ignored by buddha engine)
    b.add_argument("--width", type=int, default=1920)
    b.add_argument("--height", type=int, default=1080)
    b.add_argument("--force", action="store_true")
    b.add_argument("--skip-generate", action="store_true")
    b.add_argument("--continue-on-error", action="store_true")
    b.add_argument("--max-gen-attempts", type=int, default=2)
    b.add_argument("--export-flat", action="store_true")
    b.add_argument("--flat-name-suffix", default="thumb")
    b.add_argument("--sleep-sec", type=float, default=0.25)
    b.add_argument("--bg-brightness", type=float, default=1.0)
    b.add_argument("--bg-contrast", type=float, default=1.0)
    b.add_argument("--bg-color", type=float, default=1.0)
    b.add_argument("--bg-gamma", type=float, default=1.0)

    # buddha args (ignored by layer_specs engine)
    b.add_argument("--base", help="Base background image path for buddha_3line")
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
    r.add_argument("--width", type=int, default=1920)
    r.add_argument("--height", type=int, default=1080)
    r.add_argument("--skip-generate", action="store_true")
    r.add_argument("--continue-on-error", action="store_true")
    r.add_argument("--max-gen-attempts", type=int, default=2)
    r.add_argument("--no-export-flat", action="store_false", dest="export_flat", default=True)
    r.add_argument("--flat-name-suffix", default="thumb")
    r.add_argument("--sleep-sec", type=float, default=0.25)
    r.add_argument("--bg-brightness", type=float, default=1.0)
    r.add_argument("--bg-contrast", type=float, default=1.0)
    r.add_argument("--bg-color", type=float, default=1.0)
    r.add_argument("--bg-gamma", type=float, default=1.0)
    r.add_argument("--base", help="Base background image path for buddha_3line (if needed)")
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
    q.add_argument("--source-name", default="00_thumb.png", help="Relative name under assets/{CH}/{NNN}/ (default: 00_thumb.png)")
    q.add_argument("--tile-w", type=int, default=640)
    q.add_argument("--tile-h", type=int, default=360)
    q.add_argument("--cols", type=int, default=6)
    q.add_argument("--pad", type=int, default=8)

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

#!/usr/bin/env python3
"""
pages_video_images_previews.py — GitHub Pages用「動画内画像プレビュー」をdocs/へ生成して配信可能にする

背景（なぜ必要か）:
- `workspaces/video/runs/**/images/*.png` はローカル生成物で、gitignore対象のため Pages / raw から直接参照できない
- その結果、スマホで「動画内画像（画面）」を確認できず運用が詰まる
- そこで、Pagesで配信できる軽量プレビューを `docs/media/video_images/` に生成する

出力:
- `docs/media/video_images/CHxx/NNN/0001.jpg`（画像キューのプレビュー）
- `docs/data/video_images_index.json`（Pages側が参照できる簡易インデックス）

Usage:
  # 例: 特定回だけ生成
  python3 scripts/ops/pages_video_images_previews.py --channel CH12 --video 016 --write

  # 例: チャンネル配下をまとめて生成（注意: 量が多いと時間/容量が増える）
  python3 scripts/ops/pages_video_images_previews.py --channel CH12 --write

  # 例: runs配下から見つかる全回（注意: 量が多いと時間/容量が増える）
  python3 scripts/ops/pages_video_images_previews.py --all --write
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from _bootstrap import bootstrap

try:
    from PIL import Image, ImageOps
except Exception:  # pragma: no cover
    Image = None  # type: ignore[assignment]
    ImageOps = None  # type: ignore[assignment]


CHANNEL_RE = re.compile(r"^CH\d{2}$")
VIDEO_RE = re.compile(r"^\d{3}$")
IMG_NAME_RE = re.compile(r"^(\d{4})\.(png|jpg|jpeg|webp)$", re.IGNORECASE)


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_channel(raw: str) -> str:
    s = str(raw or "").strip().upper()
    if re.fullmatch(r"CH\d{2}", s):
        return s
    m = re.fullmatch(r"CH(\d+)", s)
    if m:
        return f"CH{int(m.group(1)):02d}"
    return s


def _normalize_video(raw: str) -> str:
    s = str(raw or "").strip()
    if re.fullmatch(r"\d{3}", s):
        return s
    try:
        return f"{int(s):03d}"
    except Exception:
        return s


def _video_id(channel: str, video: str) -> str:
    return f"{channel}-{video}"


def _runs_root(repo_root: Path) -> Path:
    return repo_root / "workspaces" / "video" / "runs"


def _output_root(repo_root: Path) -> Path:
    return repo_root / "docs" / "media" / "video_images"


def _preview_rel(channel: str, video: str, filename: str) -> str:
    return f"media/video_images/{channel}/{video}/{filename}"


def _preview_path(repo_root: Path, channel: str, video: str, filename: str) -> Path:
    return _output_root(repo_root) / channel / video / filename


def _script_index_channels(repo_root: Path) -> set[str]:
    """
    Best-effort: derive channels from GitHub Pages Script Viewer index.
    This avoids generating previews for channels that don't have scripts on Pages.
    """

    path = repo_root / "docs" / "data" / "index.json"
    if not path.exists():
        return set()
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        items = obj.get("items") if isinstance(obj, dict) else None
        if not isinstance(items, list):
            return set()
        out: set[str] = set()
        for it in items:
            if not isinstance(it, dict):
                continue
            ch = _normalize_channel(str(it.get("channel") or ""))
            if CHANNEL_RE.match(ch):
                out.add(ch)
        return out
    except Exception:
        return set()


def _index_path(repo_root: Path) -> Path:
    return repo_root / "docs" / "data" / "video_images_index.json"


def _load_existing_index_items(repo_root: Path) -> list[dict[str, object]]:
    """
    Best-effort: load existing docs/data/video_images_index.json and return items list.
    Keep unknown fields as-is (forward compatible).
    """
    path = _index_path(repo_root)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("items") if isinstance(data, dict) else None
        return items if isinstance(items, list) else []
    except Exception:
        return []


def _merge_index_items(
    *,
    existing_items: list[dict[str, object]],
    new_items: list[dict[str, object]],
) -> list[dict[str, object]]:
    by_id: dict[str, dict[str, object]] = {}
    for it in existing_items:
        if not isinstance(it, dict):
            continue
        vid = str(it.get("video_id") or "").strip()
        if not vid:
            continue
        by_id[vid] = it
    for it in new_items:
        if not isinstance(it, dict):
            continue
        vid = str(it.get("video_id") or "").strip()
        if not vid:
            continue
        by_id[vid] = it
    return list(by_id.values())


def _resolve_run_dir(repo_root: Path, channel: str, video: str) -> Path | None:
    """
    Best-effort: pick the latest run dir for CHxx-NNN.
    - Prefer direct dir: workspaces/video/runs/CHxx-NNN
    - Otherwise pick newest (mtime) matching prefix.
    """
    root = _runs_root(repo_root)
    base = f"{channel}-{video}"
    direct = root / base
    if direct.exists():
        return direct

    cands = [p for p in root.glob(base + "*") if p.is_dir()]
    if not cands:
        return None
    cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0]


def _load_image_cues_summary(run_dir: Path) -> dict[int, str]:
    """
    Return map: cue_index (1-based) -> short summary.
    """
    path = run_dir / "image_cues.json"
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        cues = obj.get("cues") if isinstance(obj, dict) else None
        if not isinstance(cues, list):
            return {}
        out: dict[int, str] = {}
        for c in cues:
            if not isinstance(c, dict):
                continue
            try:
                idx = int(c.get("index"))
            except Exception:
                continue
            summary = str(c.get("summary") or c.get("visual_focus") or "").strip()
            if summary:
                out[idx] = summary
        return out
    except Exception:
        return {}


def _iter_images(run_dir: Path) -> list[tuple[int, Path]]:
    images_dir = run_dir / "images"
    if not images_dir.exists():
        return []
    seen: dict[int, Path] = {}
    for p in sorted(images_dir.iterdir()):
        if not p.is_file():
            continue
        m = IMG_NAME_RE.match(p.name)
        if not m:
            continue
        idx = int(m.group(1))
        if idx not in seen:
            seen[idx] = p
    return sorted([(idx, p) for idx, p in seen.items()], key=lambda t: t[0])


def _write_preview_jpg(*, src: Path, dest: Path, width: int, quality: int) -> None:
    if Image is None or ImageOps is None:  # pragma: no cover
        raise RuntimeError("Pillow (PIL) is required to generate previews.")

    with Image.open(src) as im0:
        im = ImageOps.exif_transpose(im0)
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            bg = Image.new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")

        im.thumbnail((int(width), 10_000_000), resample=Image.Resampling.LANCZOS)

        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        im.save(tmp, format="JPEG", quality=int(quality), optimize=True, progressive=True)
        tmp.replace(dest)


@dataclass(frozen=True)
class VideoImagesIndexItem:
    video_id: str
    channel: str
    video: str
    run_id: str
    count: int
    files: list[dict[str, str]]


def _iter_targets(
    repo_root: Path,
    *,
    channels: set[str],
    videos: set[str],
    all_items: bool,
) -> Iterable[tuple[str, str, Path]]:
    root = _runs_root(repo_root)
    if all_items:
        for p in sorted([x for x in root.iterdir() if x.is_dir()]):
            m = re.match(r"^(CH\d{2})-(\d{3})(?:$|\D)", p.name)
            if not m:
                continue
            ch = _normalize_channel(m.group(1))
            vv = _normalize_video(m.group(2))
            if not (CHANNEL_RE.match(ch) and VIDEO_RE.match(vv)):
                continue
            yield (ch, vv, p)
        return

    # explicit filter
    if videos and not channels:
        raise SystemExit("Specify --channel when using --video.")

    for ch in sorted(channels):
        if not CHANNEL_RE.match(ch):
            continue
        if videos:
            for vv in sorted(videos):
                if not VIDEO_RE.match(vv):
                    continue
                run_dir = _resolve_run_dir(repo_root, ch, vv)
                if run_dir:
                    yield (ch, vv, run_dir)
            continue

        # channel-only: include any run dirs for that channel.
        for p in sorted([x for x in root.glob(f"{ch}-*") if x.is_dir()]):
            m = re.match(rf"^{re.escape(ch)}-(\d{{3}})(?:$|\D)", p.name)
            if not m:
                continue
            vv = _normalize_video(m.group(1))
            if VIDEO_RE.match(vv):
                yield (ch, vv, p)


def build_video_images_index(
    repo_root: Path,
    *,
    channels: set[str],
    videos: set[str],
    all_items: bool,
) -> list[VideoImagesIndexItem]:
    items: list[VideoImagesIndexItem] = []
    seen_vid: set[str] = set()
    for ch, vv, run_dir in _iter_targets(repo_root, channels=channels, videos=videos, all_items=all_items):
        vid = _video_id(ch, vv)
        if vid in seen_vid:
            continue
        seen_vid.add(vid)

        run_dir_latest = _resolve_run_dir(repo_root, ch, vv) or run_dir
        images = _iter_images(run_dir_latest)
        if not images:
            continue
        summaries = _load_image_cues_summary(run_dir_latest)

        files: list[dict[str, str]] = []
        for idx, _src in images:
            filename = f"{idx:04d}.jpg"
            files.append(
                {
                    "file": filename,
                    "rel": _preview_rel(ch, vv, filename),
                    "summary": summaries.get(idx, ""),
                }
            )

        items.append(
            VideoImagesIndexItem(
                video_id=vid,
                channel=ch,
                video=vv,
                run_id=str(run_dir_latest.name),
                count=len(files),
                files=files,
            )
        )
    return items


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate publishable in-video image previews for GitHub Pages.")
    ap.add_argument("--all", action="store_true", help="Process all run dirs under workspaces/video/runs (can be large)")
    ap.add_argument(
        "--channels-from-script-index",
        action="store_true",
        help="Use channels from docs/data/index.json (Script Viewer). Good default for Pages.",
    )
    ap.add_argument("--channel", action="append", default=[], help="Channel code (repeatable). e.g. CH12")
    ap.add_argument("--video", action="append", default=[], help="Video number (repeatable). e.g. 016")
    ap.add_argument("--width", type=int, default=640, help="Preview max width (default: 640)")
    ap.add_argument("--quality", type=int, default=82, help="JPEG quality (default: 82)")
    ap.add_argument("--overwrite", action="store_true", help="Overwrite existing previews")
    ap.add_argument("--write", action="store_true", help="Write previews + video_images_index.json (default: dry-run)")
    args = ap.parse_args()

    repo_root = Path(bootstrap(load_env=False))

    all_items = bool(args.all)
    if bool(args.channels_from_script_index):
        if all_items:
            ap.error("Do not combine --all with --channels-from-script-index.")
        if args.channel or args.video:
            ap.error("Do not combine --channels-from-script-index with --channel/--video.")
        channels = _script_index_channels(repo_root)
        videos: set[str] = set()
        if not channels:
            ap.error("docs/data/index.json is missing or empty; cannot derive channels.")
    else:
        channels = {_normalize_channel(x) for x in (args.channel or []) if str(x or "").strip()}
        videos = {_normalize_video(x) for x in (args.video or []) if str(x or "").strip()}

    if not all_items and not (channels or videos):
        ap.error("Specify --all OR at least one of --channel/--video.")

    idx_items = build_video_images_index(repo_root, channels=channels, videos=videos, all_items=all_items)
    if not idx_items:
        print("[pages_video_images_previews] no targets (no runs/images found).")
        return 0

    written = 0
    skipped_exists = 0
    missing_src = 0

    if args.write:
        for it in idx_items:
            run_dir = _resolve_run_dir(repo_root, it.channel, it.video) or (_runs_root(repo_root) / it.run_id)
            images = _iter_images(run_dir)
            src_by_idx = {idx: p for idx, p in images}
            for f in it.files:
                filename = str(f.get("file") or "").strip()
                m = re.fullmatch(r"(\d{4})\.jpg", filename)
                if not m:
                    continue
                idx = int(m.group(1))
                src = src_by_idx.get(idx)
                if not src or not src.exists():
                    missing_src += 1
                    continue
                dest = _preview_path(repo_root, it.channel, it.video, filename)
                if dest.exists() and not args.overwrite:
                    skipped_exists += 1
                    continue
                _write_preview_jpg(src=src, dest=dest, width=int(args.width), quality=int(args.quality))
                written += 1

    if args.write:
        new_items: list[dict[str, object]] = [
            {
                "video_id": it.video_id,
                "channel": it.channel,
                "video": it.video,
                "run_id": it.run_id,
                "count": it.count,
                "files": it.files,
            }
            for it in idx_items
        ]
        existing_items = _load_existing_index_items(repo_root)
        merged_items = _merge_index_items(existing_items=existing_items, new_items=new_items)
        out_payload = {
            "version": 1,
            "updated_at": _now_iso_utc(),
            "count": len(merged_items),
            "items": merged_items,
        }
        out = _index_path(repo_root)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(out_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    mode = "WRITE" if args.write else "DRY"
    print(
        f"[pages_video_images_previews] mode={mode} targets={len(idx_items)} written={written} skipped_exists={skipped_exists} missing_src={missing_src}"
    )
    if not args.write:
        print("Dry-run only. Re-run with --write to generate previews and index.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

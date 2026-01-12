#!/usr/bin/env python3
"""
pages_thumb_placeholders.py — GitHub Pages用「サムネ未生成」プレースホルダを生成する

目的:
- snapshot / Script Viewer でサムネ画像が「割れない」ことを優先する（モバイル運用の詰まり防止）。
- thumbnails/projects.json に未登録なチャンネル（例: CH17-CH21）でも、planning CSV は存在するため、
  `docs/media/thumbs/CHxx/NNN.jpg` を最低限埋めて閲覧性を担保する。

出力:
- `docs/media/thumbs/CHxx/NNN.jpg`（プレースホルダ）

注意:
- これは「本サムネ」ではない。サムネ生成パイプライン未整備の回の暫定表示。
- secrets は扱わない。

Usage:
  # 全チャンネル（planning CSV）を対象に不足分だけ生成
  python3 scripts/ops/pages_thumb_placeholders.py --all --write

  # 一部だけ
  python3 scripts/ops/pages_thumb_placeholders.py --channel CH17 --write
"""

from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from _bootstrap import bootstrap

try:
    from PIL import Image, ImageDraw
except Exception:  # pragma: no cover
    Image = None  # type: ignore[assignment]
    ImageDraw = None  # type: ignore[assignment]

bootstrap(load_env=False)

from factory_common.paths import channels_csv_path, repo_root  # noqa: E402


CHANNEL_RE = re.compile(r"^CH\d{2}$")
VIDEO_RE = re.compile(r"^\d{3}$")


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


def _thumb_preview_path(*, repo: Path, channel: str, video: str) -> Path:
    return repo / "docs" / "media" / "thumbs" / channel / f"{video}.jpg"


def _iter_planning_rows(path: Path) -> Iterable[dict[str, str]]:
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8-sig")
    reader = csv.DictReader(raw.splitlines())
    rows: list[dict[str, str]] = []
    for row in reader:
        if not isinstance(row, dict):
            continue
        rows.append({str(k or "").strip(): str(v or "").strip() for k, v in row.items() if k})
    return rows


@dataclass(frozen=True)
class Target:
    channel: str
    video: str


def _targets_for_channel(ch: str) -> list[Target]:
    csv_path = channels_csv_path(ch)
    rows = list(_iter_planning_rows(csv_path))
    out: list[Target] = []
    for row in rows:
        raw_video = row.get("動画番号") or row.get("No.") or ""
        if not raw_video.strip():
            continue
        try:
            v = _normalize_video(raw_video)
        except Exception:
            continue
        if VIDEO_RE.match(v):
            out.append(Target(channel=ch, video=v))
    # stable order
    out.sort(key=lambda t: int(t.video))
    return out


def _render_placeholder(*, video_id: str, size: tuple[int, int]) -> "Image.Image":
    if Image is None or ImageDraw is None:  # pragma: no cover
        raise RuntimeError("Pillow (PIL) is required to generate placeholder thumbnails.")
    w, h = size
    im = Image.new("RGB", (w, h), (14, 18, 32))
    d = ImageDraw.Draw(im)

    # Simple, font-agnostic ASCII only (mobile readability + no font dependencies).
    lines = [
        video_id,
        "THUMBNAIL PREVIEW",
        "NOT GENERATED YET",
    ]
    y = int(h * 0.32)
    for line in lines:
        # default font; keep centered
        bbox = d.textbbox((0, 0), line)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        x = max(10, (w - tw) // 2)
        d.text((x, y), line, fill=(235, 238, 255))
        y += th + 10
    # border
    d.rectangle([6, 6, w - 6, h - 6], outline=(110, 168, 255), width=2)
    return im


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate placeholder thumbs for GitHub Pages from Planning CSV.")
    ap.add_argument("--all", action="store_true", help="All planning channels (workspaces/planning/channels/CH*.csv)")
    ap.add_argument("--channel", action="append", default=[], help="Channel code (repeatable). e.g. CH17")
    ap.add_argument("--width", type=int, default=640, help="Output width (default: 640)")
    ap.add_argument("--height", type=int, default=360, help="Output height (default: 360)")
    ap.add_argument("--overwrite", action="store_true", help="Overwrite existing placeholder JPGs")
    ap.add_argument("--write", action="store_true", help="Write files (default: dry-run)")
    args = ap.parse_args()

    repo = repo_root()
    channels: list[str] = []
    if args.all:
        planning_dir = repo / "workspaces" / "planning" / "channels"
        for p in sorted(planning_dir.glob("CH*.csv")):
            ch = _normalize_channel(p.stem)
            if CHANNEL_RE.match(ch):
                channels.append(ch)
    else:
        channels = [_normalize_channel(x) for x in (args.channel or []) if str(x or "").strip()]

    if not channels:
        ap.error("Specify --all or at least one --channel")

    width = int(args.width)
    height = int(args.height)
    written = 0
    skipped = 0

    for ch in channels:
        if not CHANNEL_RE.match(ch):
            continue
        for t in _targets_for_channel(ch):
            dest = _thumb_preview_path(repo=repo, channel=t.channel, video=t.video)
            if dest.exists() and not args.overwrite:
                skipped += 1
                continue
            if not args.write:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            im = _render_placeholder(video_id=f"{t.channel}-{t.video}", size=(width, height))
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            im.save(tmp, format="JPEG", quality=85, optimize=True, progressive=True)
            tmp.replace(dest)
            written += 1

    mode = "WRITE" if args.write else "DRY"
    print(f"[pages_thumb_placeholders] mode={mode} written={written} skipped={skipped} channels={len(channels)}")
    if not args.write:
        print("Dry-run only. Re-run with --write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


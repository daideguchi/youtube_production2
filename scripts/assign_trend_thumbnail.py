#!/usr/bin/env python3
"""Assign a trend thumbnail to a channel/video by importing & describing the asset."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

from fastapi import HTTPException

from datetime import datetime, timezone
import os

from trend_feed import find_entry, load_feed, save_feed
from env_guard import ensure_openrouter_key
from ui.backend.main import (
    import_thumbnail_library_asset,
    assign_thumbnail_library_asset,
    describe_thumbnail_library_asset,
    ThumbnailLibraryImportRequest,
    ThumbnailLibraryAssignRequest,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Assign a trend thumbnail to a planning entry.")
    parser.add_argument("channel", help="CHコード (例: CH06)")
    parser.add_argument("video", help="動画番号 (3桁) または数値")
    parser.add_argument(
        "--id",
        help="Trend entry ID. 省略時は最新（index指定）を利用",
    )
    parser.add_argument(
        "--index",
        type=int,
        default=0,
        help="ID未指定時のインデックス (0=最新)",
    )
    parser.add_argument(
        "--label",
        default=None,
        help="サムネイル案のラベル。省略時はエントリタイトル",
    )
    parser.add_argument(
        "--notes",
        default=None,
        help="割当ノートを feed に記録",
    )
    return parser.parse_args()


def normalize_video(value: str) -> str:
    digits = "".join(ch for ch in value if ch.isdigit())
    if not digits:
        raise ValueError("動画番号が正しくありません")
    return digits.zfill(3)


def main() -> None:
    args = parse_args()
    ensure_openrouter_key()
    feed = load_feed()
    entry = find_entry(feed, args.id, args.index)
    if not entry:
        raise SystemExit("指定されたトレンドエントリが見つかりませんでした")

    channel_code = args.channel.strip().upper()
    video_number = normalize_video(args.video)
    file_stub = f"trend_{entry['id']}"
    file_name = f"{file_stub}.png"
    label = args.label or entry.get("title") or "トレンド案"

    import_payload = ThumbnailLibraryImportRequest(url=entry["image_url"], file_name=file_name)
    try:
        asset = import_thumbnail_library_asset(channel_code, import_payload)
    except HTTPException as exc:  # pragma: no cover - runtime validation
        raise SystemExit(f"画像の取り込みに失敗しました: {exc.detail}")

    try:
        description = describe_thumbnail_library_asset(channel_code, Path(asset.file_name).name)
    except HTTPException as exc:
        raise SystemExit(f"サムネイル説明の生成に失敗しました: {exc.detail}")

    assign_payload = ThumbnailLibraryAssignRequest(
        video=video_number,
        label=label,
        make_selected=True,
    )
    assign_thumbnail_library_asset(channel_code, asset.file_name, assign_payload)

    entry.setdefault("assignments", []).append(
        {
            "channel": channel_code,
            "video": video_number,
            "asset": asset.file_name,
            "label": label,
            "description": description.description,
            "notes": args.notes,
            "assigned_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    entry["picked"] = True
    entry["last_channel"] = channel_code
    entry["last_video"] = video_number
    entry["last_asset"] = asset.file_name
    entry["last_description"] = description.description
    save_feed(feed)
    print(
        f"Assigned trend #{entry['id']} to {channel_code}-{video_number} as {asset.file_name}" +
        (f" ({label})" if label else "")
    )


if __name__ == "__main__":
    main()

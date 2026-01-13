#!/usr/bin/env python3
from __future__ import annotations

"""
youtube_meta.py — YouTube貼り付け用メタ（タイトル/概要欄/タグ）をCLIで出力する

SoT:
  - Planning CSV: workspaces/planning/channels/CHxx.csv
  - Channel meta: packages/script_pipeline/channels/channels_info.json

This tool is read-only by default (prints to stdout).
"""

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from _bootstrap import bootstrap

bootstrap(load_env=False)

from factory_common.paths import channels_csv_path, repo_root  # noqa: E402


class YoutubeMetaError(RuntimeError):
    pass


CHANNEL_RE = re.compile(r"^CH(\d{1,3})$", flags=re.IGNORECASE)


def _norm_channel(raw: str) -> str:
    s = str(raw or "").strip().upper()
    m = CHANNEL_RE.fullmatch(s)
    if m:
        return f"CH{int(m.group(1)):02d}"
    if s.startswith("CH") and len(s) >= 3:
        return s
    raise YoutubeMetaError(f"invalid --channel: {raw!r} (expected CHxx)")


def _norm_video(raw: str) -> str:
    s = str(raw or "").strip()
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        raise YoutubeMetaError(f"invalid --video: {raw!r} (expected NNN)")
    return f"{int(digits):03d}"


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            out: list[dict[str, str]] = []
            for row in reader:
                if not isinstance(row, dict):
                    continue
                normalized: dict[str, str] = {}
                for k, v in row.items():
                    if k is None:
                        continue
                    normalized[str(k).strip()] = str(v or "").strip()
                out.append(normalized)
            return out
    except Exception:
        return []


def _planning_video_token(row: dict[str, str]) -> str | None:
    for key in ("動画番号", "No.", "VideoNo", "VideoNumber", "video_number", "video", "Video"):
        raw = (row.get(key) or "").strip()
        if not raw:
            continue
        try:
            return _norm_video(raw)
        except Exception:
            return None
    return None


def _planning_tags_from_row(row: dict[str, str]) -> list[str]:
    raw = (row.get("tags") or row.get("Tags") or row.get("Tags (comma)") or "").strip()
    if not raw:
        return []
    out: list[str] = []
    for part in raw.split(","):
        s = str(part or "").strip()
        if s and s not in out:
            out.append(s)
    return out


def _unique_extend(dest: list[str], items: Iterable[str]) -> None:
    for raw in items:
        s = str(raw or "").strip()
        if not s:
            continue
        if s not in dest:
            dest.append(s)


@dataclass(frozen=True)
class YoutubeMeta:
    video_id: str
    title: str
    tags: list[str]
    description_episode: str
    description_channel: str
    description_full: str
    youtube_url: str
    studio_url: str
    sources: dict[str, str]

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": "ytm.youtube_meta.v1",
            "video_id": self.video_id,
            "title": self.title,
            "tags": self.tags,
            "description_episode": self.description_episode,
            "description_channel": self.description_channel,
            "description_full": self.description_full,
            "youtube_url": self.youtube_url,
            "studio_url": self.studio_url,
            "sources": self.sources,
        }


def _load_channels_info() -> list[dict[str, Any]]:
    path = repo_root() / "packages" / "script_pipeline" / "channels" / "channels_info.json"
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    return []


def _channel_meta_by_id(channel: str) -> dict[str, Any]:
    ch = _norm_channel(channel)
    for it in _load_channels_info():
        if str(it.get("channel_id") or "").strip().upper() == ch:
            return it
    return {}


def _build_urls(channel_meta: dict[str, Any]) -> tuple[str, str]:
    yt = channel_meta.get("youtube") if isinstance(channel_meta.get("youtube"), dict) else {}
    branding = channel_meta.get("branding") if isinstance(channel_meta.get("branding"), dict) else {}

    handle = str(yt.get("handle") or channel_meta.get("youtube_handle") or branding.get("handle") or "").strip()
    channel_id = str(yt.get("channel_id") or "").strip()

    youtube_url = str(yt.get("url") or branding.get("url") or "").strip()
    if not youtube_url and handle:
        youtube_url = f"https://www.youtube.com/{handle.replace('@', '').strip()}"
        youtube_url = youtube_url.replace("youtube.com/", "youtube.com/@")

    studio_url = f"https://studio.youtube.com/channel/{channel_id}/videos" if channel_id else ""
    return youtube_url, studio_url


def build_youtube_meta(*, channel: str, video: str) -> YoutubeMeta:
    ch = _norm_channel(channel)
    vv = _norm_video(video)
    video_id = f"{ch}-{vv}"

    csv_path = channels_csv_path(ch)
    rows = _read_csv_rows(csv_path)
    row: dict[str, str] | None = None
    for r in rows:
        token = _planning_video_token(r)
        if token == vv:
            row = r
            break
    if row is None:
        raise YoutubeMetaError(f"planning row not found: {video_id} (csv: {csv_path})")

    title = (row.get("タイトル") or row.get("Title") or "").strip()
    if not title:
        raise YoutubeMetaError(f"title is empty: set Planning CSV column 'タイトル' for {video_id} ({csv_path})")

    desc_lead = (row.get("説明文_リード") or row.get("description_lead") or "").strip()
    desc_body = (
        row.get("説明文_この動画でわかること")
        or row.get("説明文_本文")
        or row.get("description_body")
        or row.get("description_takeaways")
        or ""
    ).strip()
    description_episode = "\n".join([x for x in (desc_lead, desc_body) if x]).strip()

    channel_meta = _channel_meta_by_id(ch)
    description_channel = str(channel_meta.get("youtube_description") or channel_meta.get("description") or "").strip()

    description_full = ""
    if description_episode and description_channel:
        description_full = f"{description_episode}\n\n{description_channel}".strip()
    else:
        description_full = (description_episode or description_channel or "").strip()

    tags: list[str] = []
    planning_tags = _planning_tags_from_row(row)
    if planning_tags:
        _unique_extend(tags, planning_tags)
    else:
        _unique_extend(tags, [row.get("悩みタグ_メイン") or "", row.get("悩みタグ_サブ") or ""])

    defaults_raw = channel_meta.get("default_tags")
    if isinstance(defaults_raw, list):
        _unique_extend(tags, [str(x) for x in defaults_raw])
    elif isinstance(defaults_raw, str):
        _unique_extend(tags, [defaults_raw])

    youtube_url, studio_url = _build_urls(channel_meta)

    return YoutubeMeta(
        video_id=video_id,
        title=title,
        tags=tags,
        description_episode=description_episode,
        description_channel=description_channel,
        description_full=description_full,
        youtube_url=youtube_url,
        studio_url=studio_url,
        sources={
            "planning_csv": str(csv_path),
            "channels_info": str(repo_root() / "packages/script_pipeline/channels/channels_info.json"),
        },
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Print YouTube paste-ready metadata from Planning SoT + channel meta.")
    p.add_argument("--channel", required=True, help="Channel code (e.g., CH27)")
    p.add_argument("--video", required=True, help="Video number (e.g., 001)")
    p.add_argument("--json", action="store_true", help="Print JSON payload instead of text blocks.")
    p.add_argument(
        "--field",
        choices=[
            "title",
            "tags_comma",
            "description_full",
            "description_episode",
            "description_channel",
            "youtube_url",
            "studio_url",
        ],
        default="",
        help="Print only one field (for piping).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        meta = build_youtube_meta(channel=args.channel, video=args.video)
    except YoutubeMetaError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    field = str(args.field or "").strip()
    if field:
        if field == "title":
            print(meta.title)
            return 0
        if field == "tags_comma":
            print(", ".join(meta.tags))
            return 0
        if field == "description_full":
            print(meta.description_full)
            return 0
        if field == "description_episode":
            print(meta.description_episode)
            return 0
        if field == "description_channel":
            print(meta.description_channel)
            return 0
        if field == "youtube_url":
            print(meta.youtube_url)
            return 0
        if field == "studio_url":
            print(meta.studio_url)
            return 0
        print(f"[ERROR] unknown field: {field}", file=sys.stderr)
        return 2

    if bool(args.json):
        print(json.dumps(meta.to_json(), ensure_ascii=False, indent=2))
        return 0

    print(f"[youtube_meta] {meta.video_id}")
    print(f"- sources: planning_csv={meta.sources['planning_csv']} channels_info={meta.sources['channels_info']}")
    if meta.youtube_url:
        print(f"- youtube: {meta.youtube_url}")
    if meta.studio_url:
        print(f"- studio: {meta.studio_url}")
    print("")
    print("=== TITLE ===")
    print(meta.title)
    print("")
    print("=== DESCRIPTION (FULL) ===")
    print(meta.description_full)
    print("")
    print("=== TAGS (comma) ===")
    print(", ".join(meta.tags) if meta.tags else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


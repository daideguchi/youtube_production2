#!/usr/bin/env python3
"""
Bootstrap helper for the Buddha senior 5ch set (CH12–CH16).

What it does:
- Reads Planning SoT (workspaces/planning/channels/CH12..16.csv)
- Initializes missing status.json entries (workspaces/scripts/{CH}/{NNN}/status.json)
- Backfills metadata defaults needed for stable script generation (chapter_count / length targets / display name)
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple


def _bootstrap_sys_path() -> None:
    """
    Make repo-root and `packages/` importable even when running from `scripts/`.
    (Do not rely on sitecustomize.py, which is not auto-loaded in this mode.)
    """

    start = Path(__file__).resolve()
    cur = start.parent
    repo = None
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            repo = candidate
            break
    if repo is None:
        repo = cur
    for path in (repo, repo / "packages"):
        p = str(path)
        if p not in sys.path:
            sys.path.insert(0, p)


_bootstrap_sys_path()

from factory_common.paths import channels_csv_path, status_path  # noqa: E402


@dataclass(frozen=True)
class ChannelDefaults:
    display_name: str
    chapter_count: int
    target_chars_min: int
    target_chars_max: int
    script_prompt: str


DEFAULTS: Dict[str, ChannelDefaults] = {
    "CH12": ChannelDefaults(
        display_name="ブッダの黄昏夜話",
        # CH12 is story-first (4-part). Do not apply the 8-part outline used by CH13–CH16.
        chapter_count=4,
        target_chars_min=7000,
        target_chars_max=9000,
        script_prompt=(
            "【構造固定】4部構成。"
            "第1部=導入フック。問題提起。"
            "第2部=物語パート。寓話が主役。"
            "第3部=解説パート。仏教・心理学で深掘り。"
            "第4部=締め。祈り。\n"
            "【物語】物語パートが最重要。終わりには必ず次の区切り文を入れる。物語は以上です。\n"
            "【トーン】深夜ラジオ的な過度な共感や慰めは不要。淡々とした語り部。煽り・説教・断定過多は禁止。"
        ),
    ),
    "CH13": ChannelDefaults(
        display_name="ブッダの禅処方箋",
        chapter_count=8,
        target_chars_min=6500,
        target_chars_max=8500,
        script_prompt=(
            "【構造固定】全体は8パート固定（第1章=導入フック/第2章=日常シーン/第3章=問題の正体/"
            "第4章=ブッダの見立て/第5章=史実エピソード/第6章=実践3ステップ/第7章=落とし穴/第8章=締め）。\n"
            "【トーン】家族/介護/近所づきあいを“戦わずに軽くする処方箋”。相手を悪者にしない。角を立てない言い換えと線引きが主役。"
        ),
    ),
    "CH14": ChannelDefaults(
        display_name="ブッダの執着解除",
        chapter_count=8,
        target_chars_min=7000,
        target_chars_max=9000,
        script_prompt=(
            "【構造固定】全体は8パート固定（第1章=導入フック/第2章=日常シーン/第3章=問題の正体/"
            "第4章=ブッダの見立て/第5章=史実エピソード/第6章=実践3ステップ/第7章=落とし穴/第8章=締め）。\n"
            "【トーン】執着（過去/恨み/比較/お金）を“ほどく順番”を示す。スピ断定はしない。比喩は短く、手順は具体で。"
        ),
    ),
    "CH15": ChannelDefaults(
        display_name="ブッダの心胆鍛錬",
        chapter_count=8,
        target_chars_min=6500,
        target_chars_max=8500,
        script_prompt=(
            "【構造固定】全体は8パート固定（第1章=導入フック/第2章=日常シーン/第3章=問題の正体/"
            "第4章=ブッダの見立て/第5章=史実エピソード/第6章=実践3ステップ/第7章=落とし穴/第8章=締め）。\n"
            "【トーン】根性論ではなく“設計で鍛える”。叱責ではなく、今日できる一手に落とす。焦り/怒り/自分責めを静めて行動へ。"
        ),
    ),
    "CH16": ChannelDefaults(
        display_name="ブッダの老後整え方",
        chapter_count=8,
        target_chars_min=6500,
        target_chars_max=8500,
        script_prompt=(
            "【構造固定】全体は8パート固定（第1章=導入フック/第2章=日常シーン/第3章=問題の正体/"
            "第4章=ブッダの見立て/第5章=史実エピソード/第6章=実践3ステップ/第7章=落とし穴/第8章=締め）。\n"
            "【トーン】老後不安（お金/健康/片づけ）は煽らず“整える順番”へ。投資・医療の断定は禁止。安心で終える。"
        ),
    ),
}


def _parse_channels(value: Optional[str]) -> List[str]:
    if not value:
        return sorted(DEFAULTS.keys())
    raw = [v.strip().upper() for v in value.split(",") if v.strip()]
    unknown = [ch for ch in raw if ch not in DEFAULTS]
    if unknown:
        raise SystemExit(f"Unsupported channel(s): {', '.join(unknown)} (allowed: {', '.join(sorted(DEFAULTS))})")
    return raw


def _parse_video_filter(value: Optional[str]) -> Optional[Set[str]]:
    if not value:
        return None
    out: Set[str] = set()
    for token in [t.strip() for t in value.split(",") if t.strip()]:
        if "-" in token:
            lo_s, hi_s = token.split("-", 1)
            if not lo_s.strip().isdigit() or not hi_s.strip().isdigit():
                raise SystemExit(f"Invalid --videos range: {token}")
            lo, hi = int(lo_s), int(hi_s)
            if lo > hi:
                lo, hi = hi, lo
            for n in range(lo, hi + 1):
                out.add(f"{n:03d}")
        else:
            if not token.isdigit():
                raise SystemExit(f"Invalid --videos token: {token}")
            out.add(f"{int(token):03d}")
    return out


def _iter_planning_rows(channel: str) -> Iterable[Tuple[str, str]]:
    path = channels_csv_path(channel)
    if not path.exists():
        raise SystemExit(f"Planning CSV not found: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            video_raw = (row.get("動画番号") or "").strip()
            title = (row.get("タイトル") or "").strip()
            if not video_raw or not video_raw.isdigit() or not title:
                continue
            yield f"{int(video_raw):03d}", title


def _ensure_status_entry(channel: str, video: str, title: str, *, dry_run: bool) -> None:
    if status_path(channel, video).exists():
        return
    if dry_run:
        return
    from script_pipeline.runner import ensure_status

    ensure_status(channel, video, title)


def _patch_metadata(channel: str, video: str, defaults: ChannelDefaults, *, dry_run: bool, force: bool) -> bool:
    path = status_path(channel, video)
    if not path.exists():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        data["metadata"] = metadata

    changed = False

    def _should_override(key: str, current: object, new: object) -> bool:
        if force:
            return True
        if key not in metadata or current in (None, "", 0):
            return True
        # Fix legacy/wrong defaults for CH12 without requiring --force.
        if channel == "CH12" and key == "chapter_count" and current == 8 and new == 4:
            return True
        if channel == "CH12" and key == "script_prompt" and isinstance(current, str) and "8パート固定" in current:
            return True
        return False

    def _set(key: str, value: object) -> None:
        nonlocal changed
        current = metadata.get(key)
        if _should_override(key, current, value) and current != value:
            metadata[key] = value
            changed = True

    _set("channel_display_name", defaults.display_name)
    _set("chapter_count", defaults.chapter_count)
    _set("target_chars_min", defaults.target_chars_min)
    _set("target_chars_max", defaults.target_chars_max)
    _set("target_word_count", defaults.target_chars_max)
    _set("script_prompt", defaults.script_prompt)

    if changed and not dry_run:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return changed


def _run(channels: List[str], videos: Optional[Set[str]], *, init_missing: bool, patch_metadata: bool, dry_run: bool, force: bool) -> None:
    total = 0
    inited = 0
    patched = 0
    for ch in channels:
        defaults = DEFAULTS[ch]
        for video, title in _iter_planning_rows(ch):
            if videos is not None and video not in videos:
                continue
            total += 1
            if init_missing and not status_path(ch, video).exists():
                _ensure_status_entry(ch, video, title, dry_run=dry_run)
                inited += 1
            if patch_metadata:
                if _patch_metadata(ch, video, defaults, dry_run=dry_run, force=force):
                    patched += 1

    suffix = " (dry-run)" if dry_run else ""
    print(f"targets: {total}{suffix}")
    if init_missing:
        print(f"status init: {inited}{suffix}")
    if patch_metadata:
        print(f"metadata patched: {patched}{suffix}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare CH12–CH16 for script mass production (init + metadata backfill).")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--channels", help="Comma-separated channel codes (default: CH12-CH16)")
        p.add_argument("--videos", help="Video filter: e.g. 1-30 or 1-3,10,12", default=None)
        p.add_argument("--dry-run", action="store_true", help="Do not write files")
        p.add_argument("--force", action="store_true", help="Override existing metadata fields")

    p_prepare = sub.add_parser("prepare", help="Init missing status + backfill metadata defaults")
    add_common(p_prepare)

    p_init = sub.add_parser("init", help="Init missing status.json entries from planning CSV")
    add_common(p_init)

    p_patch = sub.add_parser("patch", help="Backfill metadata defaults only")
    add_common(p_patch)

    args = parser.parse_args()
    channels = _parse_channels(args.channels)
    videos = _parse_video_filter(args.videos)

    if args.command == "prepare":
        _run(channels, videos, init_missing=True, patch_metadata=True, dry_run=args.dry_run, force=args.force)
        return
    if args.command == "init":
        _run(channels, videos, init_missing=True, patch_metadata=False, dry_run=args.dry_run, force=args.force)
        return
    if args.command == "patch":
        _run(channels, videos, init_missing=False, patch_metadata=True, dry_run=args.dry_run, force=args.force)
        return

    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()

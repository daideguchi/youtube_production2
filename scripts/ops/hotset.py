#!/usr/bin/env python3
"""
hotset.py — Hot(未投稿) / Freeze(未投稿だが当面触らない) の一覧と明示管理

SSOT:
  - ssot/ops/OPS_HOTSET_POLICY.md

Policy:
  - Hot = 未投稿（進捗=投稿済み/公開済み 以外）かつ Freeze に入っていない
  - Freeze は人間の明示のみ（推論で入れない）
  - このツールは削除/移動をしない（分類と一覧のみ）
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from _bootstrap import bootstrap

REPO_ROOT = bootstrap(load_env=False)

from factory_common.paths import channels_csv_path, planning_root, status_path  # noqa: E402


FREEZE_SCHEMA = "ytm.hotset_freeze.v1"


def _now_iso_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _z3(video: str) -> str:
    return str(video).zfill(3)


def _norm_channel(raw: str) -> str:
    s = str(raw or "").strip().upper()
    if not s.startswith("CH"):
        raise SystemExit(f"Invalid channel: {raw!r} (expected CHxx)")
    if s == "CH":
        raise SystemExit(f"Invalid channel: {raw!r} (expected CHxx)")
    # Keep as-is (CH01/CH1 both appear historically; normalize to CHxx when numeric).
    digits = "".join(ch for ch in s[2:] if ch.isdigit())
    if digits:
        return f"CH{int(digits):02d}"
    return s


def _norm_video(raw: str) -> str:
    token = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if not token:
        raise SystemExit(f"Invalid video: {raw!r} (expected NNN)")
    return _z3(str(int(token)))


def _is_published_progress(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return ("投稿済み" in text) or ("公開済み" in text) or (text.lower() in {"published", "posted"})


def _is_published_by_status_json(channel: str, video: str) -> bool:
    sp = status_path(channel, video)
    if not sp.exists():
        return False
    try:
        payload = json.loads(sp.read_text(encoding="utf-8"))
    except Exception:
        return False
    meta = payload.get("metadata") if isinstance(payload, dict) else None
    return isinstance(meta, dict) and bool(meta.get("published_lock"))


def _row_video_token(row: dict[str, str]) -> Optional[str]:
    for key in ("動画番号", "No.", "video", "Video", "VideoNumber"):
        v = row.get(key)
        if not v:
            continue
        token = "".join(ch for ch in str(v) if ch.isdigit())
        if token:
            return _z3(str(int(token)))
    return None


def _row_title(row: dict[str, str]) -> str:
    for key in ("タイトル", "title", "Title"):
        v = row.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _freeze_path() -> Path:
    return planning_root() / "hotset_freeze.json"


def _load_json(path: Path) -> dict[str, Any]:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {}


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".tmp__{path.name}__{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


@dataclass(frozen=True)
class FreezeItem:
    channel: str
    video: str
    reason: str
    created_at: str
    created_by: str


def _load_freeze_items() -> dict[tuple[str, str], FreezeItem]:
    path = _freeze_path()
    data = _load_json(path)
    items: dict[tuple[str, str], FreezeItem] = {}
    raw_items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(raw_items, list):
        return items
    for it in raw_items:
        if not isinstance(it, dict):
            continue
        try:
            ch = _norm_channel(it.get("channel") or "")
            vv = _norm_video(it.get("video") or "")
            reason = str(it.get("reason") or "").strip()
            if not reason:
                continue
            created_at = str(it.get("created_at") or "").strip() or ""
            created_by = str(it.get("created_by") or "").strip() or ""
            items[(ch, vv)] = FreezeItem(
                channel=ch,
                video=vv,
                reason=reason,
                created_at=created_at,
                created_by=created_by,
            )
        except Exception:
            continue
    return items


def _save_freeze_items(items: dict[tuple[str, str], FreezeItem]) -> None:
    path = _freeze_path()
    payload = {
        "schema": FREEZE_SCHEMA,
        "updated_at": _now_iso_utc(),
        "items": [
            {
                "channel": it.channel,
                "video": it.video,
                "reason": it.reason,
                "created_at": it.created_at,
                "created_by": it.created_by,
            }
            for it in sorted(items.values(), key=lambda x: (x.channel, x.video))
        ],
    }
    _atomic_write_json(path, payload)


def _load_planning_rows(channel: str) -> list[dict[str, str]]:
    csv_path = channels_csv_path(channel)
    if not csv_path.exists():
        raise SystemExit(f"planning csv not found: {csv_path}")
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader)


def cmd_list(args: argparse.Namespace) -> int:
    ch = _norm_channel(args.channel)
    freeze = _load_freeze_items()
    rows = _load_planning_rows(ch)

    hot: list[dict[str, Any]] = []
    frozen: list[dict[str, Any]] = []
    published: list[dict[str, Any]] = []

    for row in rows:
        vid = _row_video_token(row)
        if not vid:
            continue
        progress = (row.get("進捗") or row.get("progress") or "").strip()
        title = _row_title(row)
        key = (ch, vid)
        payload = {"episode": f"{ch}-{vid}", "video": vid, "progress": progress, "title": title}

        if _is_published_progress(progress) or _is_published_by_status_json(ch, vid):
            published.append(payload)
            continue

        if key in freeze:
            item = freeze[key]
            frozen.append({**payload, "reason": item.reason})
            continue

        hot.append(payload)

    out = {
        "schema": "ytm.hotset_snapshot.v1",
        "generated_at": _now_iso_utc(),
        "channel": ch,
        "counts": {"hot": len(hot), "freeze": len(frozen), "published": len(published)},
        "hot": hot,
        "freeze": frozen,
        "published": published if bool(args.include_published) else [],
        "freeze_file": str(_freeze_path()),
    }
    if bool(args.json):
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    print(f"[hotset] channel={ch}")
    print(f"- hot={len(hot)} freeze={len(frozen)} published={len(published)}")
    print(f"- freeze_file: {_freeze_path()}")
    if hot:
        print("\n[HOT]")
        for it in hot[: int(args.limit)]:
            title = f" {it['title']}" if it.get("title") else ""
            print(f"- {it['episode']}: {it.get('progress','')}{title}")
    if frozen:
        print("\n[FREEZE]")
        for it in frozen[: int(args.limit)]:
            title = f" {it['title']}" if it.get("title") else ""
            print(f"- {it['episode']}: {it.get('reason','')}{title}")
    if bool(args.include_published) and published:
        print("\n[PUBLISHED]")
        for it in published[: int(args.limit)]:
            title = f" {it['title']}" if it.get("title") else ""
            print(f"- {it['episode']}: {it.get('progress','')}{title}")
    return 0


def cmd_freeze_add(args: argparse.Namespace) -> int:
    ch = _norm_channel(args.channel)
    vv = _norm_video(args.video)
    reason = str(args.reason or "").strip()
    if not reason:
        raise SystemExit("--reason is required (no silent freeze).")
    created_by = str(os.getenv("LLM_AGENT_NAME") or os.getenv("USER") or "unknown").strip() or "unknown"
    created_at = _now_iso_utc()

    items = _load_freeze_items()
    key = (ch, vv)
    if key in items and not bool(args.overwrite):
        raise SystemExit(f"[POLICY] already frozen: {ch}-{vv} (use --overwrite to update reason)")
    items[key] = FreezeItem(channel=ch, video=vv, reason=reason, created_at=created_at, created_by=created_by)
    _save_freeze_items(items)
    print("[OK] freeze added")
    print(f"- episode: {ch}-{vv}")
    print(f"- reason: {reason}")
    print(f"- file: {_freeze_path()}")
    return 0


def cmd_freeze_remove(args: argparse.Namespace) -> int:
    ch = _norm_channel(args.channel)
    vv = _norm_video(args.video)
    items = _load_freeze_items()
    key = (ch, vv)
    if key not in items:
        print("[OK] not frozen (noop)")
        return 0
    items.pop(key, None)
    _save_freeze_items(items)
    print("[OK] freeze removed")
    print(f"- episode: {ch}-{vv}")
    print(f"- file: {_freeze_path()}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Manage Hot(unposted)/Freeze(unposted inactive) sets (no delete).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="List hot/freeze/published for a channel")
    p_list.add_argument("--channel", required=True, help="CHxx")
    p_list.add_argument("--limit", type=int, default=50, help="Max items to print (default: 50)")
    p_list.add_argument("--include-published", action="store_true", help="Also show published section")
    p_list.add_argument("--json", action="store_true", help="Emit JSON snapshot")
    p_list.set_defaults(func=cmd_list)

    p_add = sub.add_parser("freeze-add", help="Add a Freeze entry (explicit; no guessing)")
    p_add.add_argument("--channel", required=True, help="CHxx")
    p_add.add_argument("--video", required=True, help="NNN")
    p_add.add_argument("--reason", required=True, help="Why this unposted episode is frozen (required)")
    p_add.add_argument("--overwrite", action="store_true", help="Overwrite existing reason")
    p_add.set_defaults(func=cmd_freeze_add)

    p_rm = sub.add_parser("freeze-remove", help="Remove a Freeze entry")
    p_rm.add_argument("--channel", required=True, help="CHxx")
    p_rm.add_argument("--video", required=True, help="NNN")
    p_rm.set_defaults(func=cmd_freeze_remove)

    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

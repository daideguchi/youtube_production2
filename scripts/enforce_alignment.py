#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

def _discover_repo_root(start: Path) -> Path:
    cur = start if start.is_dir() else start.parent
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate.resolve()
    return cur.resolve()


PROJECT_ROOT = _discover_repo_root(Path(__file__).resolve())
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from factory_common.alignment import (
    ALIGNMENT_SCHEMA,
    bracket_topic_overlaps,
    build_alignment_stamp,
    iter_thumbnail_catches_from_row,
    planning_signature_from_row,
    title_script_token_overlap_ratio,
    utc_now_iso,
)
from factory_common.paths import channels_csv_path, planning_root, status_path, video_root
from script_pipeline.tools.optional_fields_registry import get_planning_section, update_planning_from_row


def _norm_channel(value: str) -> str:
    return str(value or "").strip().upper()


def _norm_video(value: str) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return None
    try:
        return f"{int(digits):03d}"
    except ValueError:
        return None


def _iter_channels(selected: Optional[set[str]]) -> Iterable[str]:
    root = planning_root() / "channels"
    for p in sorted(root.glob("CH*.csv")):
        ch = p.stem.upper()
        if selected and ch not in selected:
            continue
        yield ch


def _load_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


def _load_status_payload(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_status_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _resolve_script_path(channel: str, video: str) -> Optional[Path]:
    base = video_root(channel, video) / "content"
    cand = base / "assembled_human.md"
    if cand.exists():
        return cand
    cand = base / "assembled.md"
    if cand.exists():
        return cand
    return None


@dataclass(frozen=True)
class EpisodeCheck:
    channel: str
    video: str
    ok_to_stamp: bool
    reason: str


def _check_episode(row: dict[str, str], *, channel: str) -> EpisodeCheck:
    video = _norm_video(row.get("動画番号") or row.get("No.") or "")
    if not video:
        return EpisodeCheck(channel=channel, video="(missing)", ok_to_stamp=False, reason="動画番号が取得できません")

    planning_title = str(row.get("タイトル") or "").strip()
    script_path = _resolve_script_path(channel, video)
    if not script_path:
        return EpisodeCheck(channel=channel, video=video, ok_to_stamp=False, reason="台本が存在しません")

    catches = {c for c in iter_thumbnail_catches_from_row(row)}
    if len(catches) > 1:
        return EpisodeCheck(channel=channel, video=video, ok_to_stamp=False, reason="サムネプロンプト先頭行が不一致")

    # Heuristic: if bracket-topic never overlaps, treat as suspect (likely planning drift).
    try:
        preview = script_path.read_text(encoding="utf-8")[:6000]
    except Exception:
        preview = ""

    if planning_title and not bracket_topic_overlaps(planning_title, preview):
        ratio = title_script_token_overlap_ratio(planning_title, preview)
        return EpisodeCheck(
            channel=channel,
            video=video,
            ok_to_stamp=False,
            reason=f"タイトル主要語が台本に出現しません (overlap={ratio:.2f})",
        )

    # If there is no title, still allow stamping (it will be stored as empty).
    return EpisodeCheck(channel=channel, video=video, ok_to_stamp=True, reason="ok")


def main() -> None:
    parser = argparse.ArgumentParser(description="Enforce deterministic Planning↔Script alignment stamps.")
    parser.add_argument("--channels", help="Comma separated channel codes (e.g. CH02,CH04). Omit for all.")
    parser.add_argument("--apply", action="store_true", help="Write alignment stamps into status.json")
    parser.add_argument("--force", action="store_true", help="Overwrite existing alignment stamps")
    args = parser.parse_args()

    selected = None
    if args.channels:
        selected = {_norm_channel(x) for x in args.channels.split(",") if x.strip()}

    total = 0
    stamped = 0
    skipped = 0
    suspect = 0

    for ch in _iter_channels(selected):
        csv_path = channels_csv_path(ch)
        if not csv_path.exists():
            continue
        rows = _load_csv_rows(csv_path)
        for row in rows:
            video = _norm_video(row.get("動画番号") or row.get("No.") or "") or ""
            if not video:
                continue
            total += 1
            check = _check_episode(row, channel=ch)
            st_path = status_path(ch, video)
            if not st_path.exists():
                skipped += 1
                continue
            payload = _load_status_payload(st_path)
            meta = payload.get("metadata")
            if not isinstance(meta, dict):
                meta = {}
                payload["metadata"] = meta
            existing = meta.get("alignment")
            has_stamp = isinstance(existing, dict) and existing.get("schema") == ALIGNMENT_SCHEMA
            if has_stamp and not args.force:
                skipped += 1
                continue

            script_path = _resolve_script_path(ch, video)
            if not script_path:
                skipped += 1
                continue

            if check.ok_to_stamp:
                if args.apply:
                    stamp = build_alignment_stamp(planning_row=row, script_path=script_path)
                    meta["alignment"] = stamp.as_dict()
                    # Freeze planning title snapshot used at generation time (belt/title etc).
                    planning_sig = planning_signature_from_row(row)
                    title = str(planning_sig.get("title") or "").strip()
                    if title:
                        meta["sheet_title"] = title
                    # Snapshot planning optional fields (thumb prompts etc) into status for downstream consumers.
                    planning_section = get_planning_section(meta)
                    update_planning_from_row(planning_section, row)
                    payload["updated_at"] = utc_now_iso()
                    _save_status_payload(st_path, payload)
                stamped += 1
            else:
                suspect += 1
                if args.apply:
                    meta["alignment"] = {
                        "schema": ALIGNMENT_SCHEMA,
                        "computed_at": utc_now_iso(),
                        "suspect": True,
                        "suspect_reason": check.reason,
                    }
                    # Also annotate redo_note for visibility.
                    note = str(meta.get("redo_note") or "").strip()
                    prefix = "整合NG"
                    msg = f"{prefix}: {check.reason}"
                    if not note:
                        meta["redo_note"] = msg
                    elif msg not in note:
                        meta["redo_note"] = f"{note} / {msg}"
                    meta.setdefault("redo_script", True)
                    meta.setdefault("redo_audio", True)
                    payload["updated_at"] = utc_now_iso()
                    _save_status_payload(st_path, payload)

    print(f"episodes={total} stamped={stamped} suspect={suspect} skipped={skipped} apply={bool(args.apply)} force={bool(args.force)}")


if __name__ == "__main__":
    main()

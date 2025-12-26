#!/usr/bin/env python3
"""
Normalize CapCut template JSON in-place (with backups).

Why:
  - Some CapCut templates accumulate "empty track names" / "duplicate track names".
  - pyJianYingDraft and downstream automation can mis-handle those (tracks dropped/overwritten).
  - Even if the pipeline normalizes after copying, the template itself becomes unreadable in CapCut.

This tool makes track names:
  - non-empty
  - unique within each draft JSON (draft_content.json and draft_info.json)
  - consistent across both JSON files by track id

Safety:
  - Default is dry-run.
  - When --run is specified, it creates *.bak_<timestamp> backups next to the JSON files.
"""

from __future__ import annotations

import argparse
import copy
import datetime as _dt
import json
import os
import shutil
from pathlib import Path
from typing import Any, Optional


DEFAULT_DRAFT_ROOT = (
    Path(os.getenv("CAPCUT_DRAFT_ROOT", "")).expanduser()
    if os.getenv("CAPCUT_DRAFT_ROOT")
    else Path.home() / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
)
DEFAULT_PRESETS_PATH = Path(__file__).resolve().parent.parent / "config" / "channel_presets.json"


def _try_load_json(path: Path) -> tuple[Optional[dict[str, Any]], str]:
    if not path.exists():
        return None, "missing"
    try:
        if path.stat().st_size == 0:
            return None, "empty"
    except Exception:
        return None, "stat_failed"
    try:
        return json.loads(path.read_text(encoding="utf-8")), ""
    except Exception as exc:
        return None, f"invalid:{type(exc).__name__}"


def _count_empty_dup_names(tracks: Any) -> tuple[int, int, int]:
    if not isinstance(tracks, list):
        return 0, 0, 0
    names = [(t.get("name") or "").strip() for t in tracks if isinstance(t, dict)]
    empty = sum(1 for n in names if not n)
    dup = len(names) - len(set(names))
    return len(names), empty, dup


def _normalize_track_names_in_place(tracks: list[dict[str, Any]]) -> dict[str, str]:
    used: set[str] = set()
    per_type_count: dict[str, int] = {}
    id_to_name: dict[str, str] = {}

    for tr in tracks:
        ttype = str(tr.get("type") or "track").strip() or "track"
        name = str(tr.get("name") or "").strip()
        if not name:
            per_type_count[ttype] = per_type_count.get(ttype, 0) + 1
            name = f"{ttype}_{per_type_count[ttype]}"

        base = name
        suffix = 1
        while name in used:
            suffix += 1
            name = f"{base}_{suffix}"
        used.add(name)

        tr["name"] = name
        tid = str(tr.get("id") or "").strip()
        if tid:
            id_to_name[tid] = name

    return id_to_name


def _apply_id_name_mapping(tracks: list[dict[str, Any]], id_to_name: dict[str, str]) -> None:
    for tr in tracks:
        tid = str(tr.get("id") or "").strip()
        if tid and tid in id_to_name:
            tr["name"] = id_to_name[tid]
    _normalize_track_names_in_place(tracks)


def _backup(path: Path, stamp: str) -> None:
    if not path.exists():
        return
    backup_path = path.parent / f"{path.name}.bak_{stamp}"
    if backup_path.exists():
        return
    shutil.copy2(path, backup_path)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _normalize_template_dir(template_dir: Path, *, run: bool) -> dict[str, Any]:
    content_path = template_dir / "draft_content.json"
    info_path = template_dir / "draft_info.json"

    content_data, content_reason = _try_load_json(content_path)
    info_data, info_reason = _try_load_json(info_path)

    if content_data is None and info_data is None:
        return {
            "ok": False,
            "reason": f"no_json (content={content_reason}, info={info_reason})",
            "content": content_reason,
            "info": info_reason,
        }

    if content_data is None and info_data is not None:
        content_data = copy.deepcopy(info_data)
    if info_data is None and content_data is not None:
        info_data = copy.deepcopy(content_data)

    assert content_data is not None
    assert info_data is not None

    content_tracks = content_data.get("tracks")
    info_tracks = info_data.get("tracks")
    if not isinstance(content_tracks, list):
        content_tracks = []
        content_data["tracks"] = content_tracks
    if not isinstance(info_tracks, list):
        info_tracks = []
        info_data["tracks"] = info_tracks

    before_count, before_empty, before_dup = _count_empty_dup_names(content_tracks or info_tracks)

    id_to_name = _normalize_track_names_in_place([t for t in content_tracks if isinstance(t, dict)])
    _apply_id_name_mapping([t for t in info_tracks if isinstance(t, dict)], id_to_name)

    after_count, after_empty, after_dup = _count_empty_dup_names(content_tracks or info_tracks)

    changed = (before_empty, before_dup) != (after_empty, after_dup) or (content_reason != "" or info_reason != "")

    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    if run and changed:
        _backup(content_path, stamp)
        _backup(info_path, stamp)
        _write_json(content_path, content_data)
        _write_json(info_path, info_data)

    return {
        "ok": True,
        "changed": bool(changed),
        "before": {"tracks": before_count, "empty_names": before_empty, "dup_names": before_dup},
        "after": {"tracks": after_count, "empty_names": after_empty, "dup_names": after_dup},
        "wrote": bool(run and changed),
        "content_reason": content_reason,
        "info_reason": info_reason,
        "has_content": content_path.exists(),
        "has_info": info_path.exists(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--draft-root", default=str(DEFAULT_DRAFT_ROOT), help="CapCut drafts root directory")
    ap.add_argument("--presets", default=str(DEFAULT_PRESETS_PATH), help="Path to channel_presets.json")
    ap.add_argument("--active-only", action="store_true", help="Only normalize status=active channels in presets")
    ap.add_argument("--channel", help="Limit to a single channel (e.g., CH06)")
    ap.add_argument("--template", help="Limit to a specific template folder name")
    ap.add_argument("--run", action="store_true", help="Actually modify files (default: dry-run)")
    args = ap.parse_args()

    draft_root = Path(args.draft_root).expanduser().resolve()
    presets_path = Path(args.presets).expanduser().resolve()
    presets = json.loads(presets_path.read_text(encoding="utf-8")).get("channels", {})

    targets: list[tuple[str, str]] = []
    if args.template:
        targets.append((args.channel or "__manual__", args.template))
    else:
        for channel_id, cfg in presets.items():
            status = (cfg or {}).get("status", "active")
            if args.active_only and status != "active":
                continue
            if args.channel and channel_id.upper() != args.channel.upper():
                continue
            tpl = (cfg or {}).get("capcut_template") or ""
            if tpl:
                targets.append((channel_id, tpl))

    if not targets:
        print("No target templates found.")
        return 1

    print(f"CapCut draft root: {draft_root}")
    print(f"Presets: {presets_path}")
    print(f"Mode: {'RUN (write + backups)' if args.run else 'DRY-RUN'}")
    print("")

    any_failed = False
    any_changed = False
    for channel_id, tpl in targets:
        template_dir = draft_root / tpl
        if not template_dir.exists():
            any_failed = True
            print(f"❌ [{channel_id}] missing template dir: {tpl}")
            continue
        result = _normalize_template_dir(template_dir, run=args.run)
        if not result.get("ok"):
            any_failed = True
            print(f"❌ [{channel_id}] {tpl}: {result.get('reason')}")
            continue

        before = result["before"]
        after = result["after"]
        changed = result["changed"]
        wrote = result["wrote"]
        any_changed = any_changed or bool(changed)
        print(
            f"✅ [{channel_id}] {tpl}: "
            f"tracks={before['tracks']} empty={before['empty_names']} dup={before['dup_names']} "
            f"→ empty={after['empty_names']} dup={after['dup_names']} "
            f"{'(written)' if wrote else '(dry)'}"
        )

    if args.run and not any_changed:
        print("\nNo changes needed.")

    return 1 if any_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

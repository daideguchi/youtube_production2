#!/usr/bin/env python3
"""
Validate CapCut templates referenced by config/channel_presets.json.

This is a safety net for "wrong template used" / "layer chaos" incidents:
  - Detect missing template dirs / missing JSON files
  - Detect templates with empty tracks
  - Report empty/duplicate track names (pyJianYingDraft may drop/overwrite them)

Exit code:
  - 0: all ACTIVE channels pass minimum checks
  - 1: at least one ACTIVE channel is broken
"""

from __future__ import annotations

import argparse
import json
import os
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


def _summarize_draft_dir(draft_dir: Path) -> dict[str, Any]:
    content_path = draft_dir / "draft_content.json"
    info_path = draft_dir / "draft_info.json"

    content_data, content_reason = _try_load_json(content_path)
    info_data, info_reason = _try_load_json(info_path)

    data = content_data or info_data
    source = "draft_content.json" if content_data else ("draft_info.json" if info_data else None)
    tracks = (data or {}).get("tracks") if isinstance(data, dict) else None
    tracks = tracks if isinstance(tracks, list) else []

    names = [(t.get("name") or "").strip() for t in tracks if isinstance(t, dict)]
    empty_names = sum(1 for n in names if not n)
    dup_names = len(names) - len(set(names))

    return {
        "dir_exists": draft_dir.exists(),
        "has_content": content_path.exists() and content_path.stat().st_size > 0 if content_path.exists() else False,
        "has_info": info_path.exists() and info_path.stat().st_size > 0 if info_path.exists() else False,
        "content_reason": content_reason,
        "info_reason": info_reason,
        "json_source": source,
        "tracks": len(tracks),
        "empty_track_names": empty_names,
        "duplicate_track_names": dup_names,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--draft-root", default=str(DEFAULT_DRAFT_ROOT), help="CapCut drafts root directory")
    ap.add_argument("--presets", default=str(DEFAULT_PRESETS_PATH), help="Path to channel_presets.json")
    ap.add_argument("--json", action="store_true", help="Output JSON summary")
    ap.add_argument("--active-only", action="store_true", help="Only report status=active channels")
    ap.add_argument(
        "--strict-names",
        action="store_true",
        help="Fail if ACTIVE templates have empty/duplicate track names",
    )
    args = ap.parse_args()

    draft_root = Path(args.draft_root).expanduser().resolve()
    presets_path = Path(args.presets).expanduser().resolve()

    presets = json.loads(presets_path.read_text(encoding="utf-8")).get("channels", {})
    rows: list[dict[str, Any]] = []
    failed_active = False

    for channel_id, cfg in presets.items():
        status = (cfg or {}).get("status", "active")
        if args.active_only and status != "active":
            continue
        template = (cfg or {}).get("capcut_template") or ""
        draft_dir = draft_root / template if template else draft_root / "__MISSING__"

        summary = _summarize_draft_dir(draft_dir)
        summary.update(
            {
                "channel": channel_id,
                "status": status,
                "template": template,
                "draft_dir": str(draft_dir),
            }
        )

        # Minimum checks for ACTIVE channels
        if status == "active":
            has_any_json = summary["has_content"] or summary["has_info"]
            if not template or not summary["dir_exists"] or not has_any_json or summary["tracks"] == 0:
                failed_active = True
            if args.strict_names and (summary["empty_track_names"] > 0 or summary["duplicate_track_names"] > 0):
                failed_active = True

        rows.append(summary)

    if args.json:
        print(json.dumps({"draft_root": str(draft_root), "rows": rows}, ensure_ascii=False, indent=2))
    else:
        print(f"CapCut draft root: {draft_root}")
        print(f"Presets: {presets_path}")
        print("")
        print("CH   | status | template | dir | content | info | tracks | empty_names | dup_names | json_source")
        print("-----+--------+----------+-----+---------+------+--------+------------+----------+-----------")
        for r in rows:
            print(
                f"{r['channel']:>4} | {r['status']:<6} | {r['template'] or '-':<8} | "
                f"{'OK' if r['dir_exists'] else 'NG':<3} | "
                f"{'Y' if r['has_content'] else 'N':<7} | "
                f"{'Y' if r['has_info'] else 'N':<4} | "
                f"{r['tracks']:>6} | {r['empty_track_names']:>10} | {r['duplicate_track_names']:>8} | "
                f"{r['json_source'] or '-'}"
            )

    return 1 if failed_active else 0


if __name__ == "__main__":
    raise SystemExit(main())

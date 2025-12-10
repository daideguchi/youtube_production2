#!/usr/bin/env python3
"""Validate channels CSV persona fields and tag completeness."""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLANNING_CSV = PROJECT_ROOT / "progress" / "channels CSV"

KNOWN_CHANNELS = ["CH01", "CH02", "CH03", "CH04", "CH05", "CH06"]

from commentary_01_srtfile_v2.core.tools import planning_requirements


def _extract_numeric_value(raw: Optional[str]) -> Optional[int]:
    if not raw:
        return None
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def load_persona_map() -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for code in KNOWN_CHANNELS:
        persona = planning_requirements.get_channel_persona(code)
        if persona:
            mapping[code] = persona
    return mapping


def read_planning_rows() -> List[dict]:
    if not PLANNING_CSV.exists():
        raise FileNotFoundError(f"channels CSV not found: {PLANNING_CSV}")
    with PLANNING_CSV.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        return list(reader)


def main() -> int:
    persona_map = load_persona_map()
    if not persona_map:
        print("No persona files detected.")
        return 1

    rows = read_planning_rows()
    mismatches: List[str] = []
    missing_tags: List[str] = []

    for row in rows:
        code = (row.get("チャンネル") or "").strip().upper()
        if not code or code not in persona_map:
            continue
        persona_value = (row.get("ターゲット層") or "").strip()
        expected = persona_map[code]
        if persona_value != expected:
            descriptor = row.get("動画ID") or f"{code}-{row.get('動画番号')}"
            mismatches.append(f"{descriptor}: ターゲット層が固定ペルソナと一致しません")

        video_no = _extract_numeric_value(row.get("No.") or row.get("動画番号"))
        required_columns = planning_requirements.resolve_required_columns(code, video_no)
        if required_columns:
            empty_cols = [col for col in required_columns if not (row.get(col) or "").strip()]
            if empty_cols:
                descriptor = row.get("動画ID") or f"{code}-{row.get('動画番号')}"
                missing_tags.append(f"{descriptor}: 未入力列 -> {', '.join(empty_cols)}")

    if mismatches or missing_tags:
        print("❌ channels CSV の固定ペルソナ／タグ検証に失敗しました")
        if mismatches:
            print("--- ペルソナ不一致 ---")
            for item in mismatches:
                print("  -", item)
        if missing_tags:
            print("--- 必須タグ未入力 (CH01 No.>=191) ---")
            for item in missing_tags:
                print("  -", item)
        return 1

    print("✅ channels CSV のペルソナ列とタグ列は要件を満たしています。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

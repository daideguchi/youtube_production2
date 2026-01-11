#!/usr/bin/env python3
"""
全チャンネルの台本（assembled.md）と planning CSV / status.json を同期するワンショット。
- workspaces/planning/channels/*.csv を走査（`factory_common.paths.planning_root()`）
- 対応する workspaces/scripts/{CH}/0xx/content/assembled.md があれば（`factory_common.paths.script_data_root()`）
  - CSV: 台本パス、文字数、進捗（未設定なら script_validated）を更新
  - status.json: sheet_title/expected_title/title を Planning SoT（CSV）から補完（既存ステージは保持）
"""

import csv
import json
import sys
import argparse
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import re

from _bootstrap import bootstrap


bootstrap(load_env=False)

from factory_common.paths import planning_root, repo_root, script_data_root

CHANNELS_DIR = planning_root() / "channels"
DATA_ROOT = script_data_root()

def _to_repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root()).as_posix()
    except Exception:
        return path.as_posix()

def _parse_int_no(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def sort_rows_inplace(csv_path: Path, rows: List[Dict[str, str]]) -> None:
    """
    Sort planning rows by numeric 'No.' while keeping non-numeric rows
    (memos/separators) immediately after the previous numeric row.
    """
    last_numeric = 0
    keyed: List[Tuple[Tuple[int, int, int], Dict[str, str]]] = []
    non_numeric_count = 0

    for idx, row in enumerate(rows):
        raw_no = row.get("No.")
        parsed = _parse_int_no(raw_no)
        if parsed is None:
            if raw_no and raw_no.strip():
                non_numeric_count += 1
            keyed.append(((last_numeric, 1, idx), row))
            continue
        last_numeric = parsed
        keyed.append(((parsed, 0, idx), row))

    keyed.sort(key=lambda item: item[0])
    rows[:] = [row for _, row in keyed]
    if non_numeric_count:
        print(
            f"[WARN] {csv_path.name}: found {non_numeric_count} non-numeric 'No.' rows; preserved as memo/separator rows",
            file=sys.stderr,
        )


def load_csv(path: Path) -> Tuple[List[Dict[str, str]], List[str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or []
    return rows, fieldnames


def save_csv(path: Path, rows: List[Dict[str, str]], fieldnames: List[str]) -> None:
    # 既存フィールドに加え、行に現れた追加キーも書き出す
    extra_keys = set()
    for row in rows:
        extra_keys.update(row.keys())
    all_fields = fieldnames[:]
    for key in sorted(extra_keys):
        if key not in all_fields:
            all_fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=all_fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def read_status(path: Path) -> Dict:
    return json.loads(path.read_text()) if path.exists() else {}


def write_status(path: Path, payload: Dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))

_RE_PAUSE_LINE = re.compile(r"^\s*---\s*$")


def _a_text_char_count(text: str) -> int:
    """
    Count "spoken" characters (match script_pipeline.validator._a_text_char_count):
    - exclude pause-only lines (`---`)
    - exclude whitespace/newlines
    """
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines: List[str] = []
    for line in normalized.split("\n"):
        if _RE_PAUSE_LINE.match(line):
            continue
        lines.append(line)
    compact = "".join(lines)
    compact = compact.replace(" ", "").replace("\t", "").replace("\u3000", "")
    return len(compact.strip())


def _find_row(rows: List[Dict[str, str]], no: int) -> Optional[Dict[str, str]]:
    """
    Planning CSV is the SoT. This sync must NOT create new planning rows,
    and must NOT infer titles from A-text.
    """
    for r in rows:
        parsed = _parse_int_no(r.get("No.")) or _parse_int_no(r.get("動画番号"))
        if parsed == no:
            return r
    return None


def sync_one(code: str, no: int, rows: List[Dict[str, str]], fieldnames: List[str], data_dir: Path) -> None:
    base_dir = data_dir / f"{no:03d}"
    content_path = base_dir / "content" / "assembled.md"
    if not content_path.exists():
        # Prefer SoT (assembled_human) if present; keep CSV path on assembled.md for compatibility.
        alt = base_dir / "content" / "assembled_human.md"
        if not alt.exists():
            return
        content_path = alt
    row = _find_row(rows, no)
    if row is None:
        print(f"[WARN] {code} {no:03d}: planning row not found; skip (no auto-create)", file=sys.stderr)
        return

    text = content_path.read_text(encoding="utf-8")
    char_count = _a_text_char_count(text)
    length = str(char_count)

    row_title = str(row.get("タイトル") or "").strip()

    status_path = base_dir / "status.json"
    status = read_status(status_path)
    md = status.get("metadata") if isinstance(status.get("metadata"), dict) else {}
    # Canonical title must come from Planning SoT only.
    canonical_title = row_title

    content_rel = _to_repo_relative(data_dir / f"{no:03d}" / "content" / "assembled.md")
    row["台本"] = content_rel
    row["台本パス"] = content_rel
    row["文字数"] = length
    row.setdefault("進捗", "script_validated")

    md.setdefault("assembled_path", content_rel)
    md.setdefault("assembled_characters", char_count)
    if canonical_title:
        # Hard repair: keep titles aligned to Planning SoT, never from A-text body.
        if str(md.get("sheet_title") or "").strip() != canonical_title:
            md["sheet_title"] = canonical_title
        if str(md.get("expected_title") or "").strip() != canonical_title:
            md["expected_title"] = canonical_title
        if str(md.get("title") or "").strip() != canonical_title:
            md["title"] = canonical_title
        if str(md.get("title_sanitized") or "").strip() != canonical_title:
            md["title_sanitized"] = canonical_title

    status.setdefault("script_id", f"{code}-{no:03d}")
    status.setdefault("channel", code)
    status.setdefault("channel_code", code)
    status.setdefault("video_number", f"{no:03d}")
    status.setdefault("status", "script_validated")
    status.setdefault("stages", {})
    status["metadata"] = md
    write_status(status_path, status)


def sync_channel(csv_path: Path) -> None:
    code = csv_path.stem.upper()
    data_dir = DATA_ROOT / code
    if not data_dir.exists():
        return
    rows, fieldnames = load_csv(csv_path)
    for entry in sorted(data_dir.iterdir()):
        if entry.is_dir() and entry.name.isdigit():
            no = int(entry.name)
            sync_one(code, no, rows, fieldnames, data_dir)
    sort_rows_inplace(csv_path, rows)
    save_csv(csv_path, rows, fieldnames)


def main() -> None:
    ap = argparse.ArgumentParser(description="Sync planning CSV + status.json from existing A-text outputs.")
    ap.add_argument(
        "--channel",
        action="append",
        help="Only sync specific channel code(s) (e.g. --channel CH07). Repeatable. Default: all channels.",
    )
    args = ap.parse_args()

    only: set[str] = set()
    if args.channel:
        for raw in args.channel:
            if not raw:
                continue
            for token in str(raw).replace(",", " ").split():
                token = token.strip().upper()
                if token:
                    only.add(token)

    for csv_path in CHANNELS_DIR.glob("*.csv"):
        if csv_path.name.lower().endswith("_planning_template.csv"):
            continue
        if only and csv_path.stem.upper() not in only:
            continue
        sync_channel(csv_path)


if __name__ == "__main__":
    main()

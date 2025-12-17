#!/usr/bin/env python3
"""
全チャンネルの台本（assembled.md）と planning CSV / status.json を同期するワンショット。
- workspaces/planning/channels/*.csv を走査（`factory_common.paths.planning_root()`）
- 対応する workspaces/scripts/{CH}/0xx/content/assembled.md があれば（`factory_common.paths.script_data_root()`）
  - CSV: タイトル（冒頭1行）、台本パス、文字数、進捗（未設定なら script_validated）を更新
  - status.json: title/assembled_path/assembled_characters 等を補完（既存ステージは保持）
"""

import csv
import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple

def _discover_repo_root(start: Path) -> Path:
    cur = start if start.is_dir() else start.parent
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate.resolve()
    return cur.resolve()


PROJECT_ROOT = _discover_repo_root(Path(__file__).resolve())
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from factory_common.paths import planning_root, script_data_root

CHANNELS_DIR = planning_root() / "channels"
DATA_ROOT = script_data_root()

def _to_repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except Exception:
        return path.as_posix()


def load_csv(path: Path) -> Tuple[List[Dict[str, str]], List[str]]:
    with path.open(encoding="utf-8", newline="") as f:
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
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=all_fields)
        writer.writeheader()
        writer.writerows(rows)


def read_status(path: Path) -> Dict:
    return json.loads(path.read_text()) if path.exists() else {}


def write_status(path: Path, payload: Dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


def ensure_row(rows: List[Dict[str, str]], fieldnames: List[str], code: str, no: int) -> Dict[str, str]:
    for r in rows:
        if r.get("No.") == str(no):
            return r
    row = {k: "" for k in fieldnames}
    row["No."] = str(no)
    row["チャンネル"] = row.get("チャンネル") or code
    row["動画番号"] = row.get("動画番号") or str(no)
    row["動画ID"] = row.get("動画ID") or f"{code}-{no:03d}"
    row["台本番号"] = row.get("台本番号") or f"{code}-{no:03d}"
    rows.append(row)
    return row


def sync_one(code: str, no: int, rows: List[Dict[str, str]], fieldnames: List[str], data_dir: Path) -> None:
    content_path = data_dir / f"{no:03d}" / "content" / "assembled.md"
    if not content_path.exists():
        return
    text = content_path.read_text()
    title = text.splitlines()[0].strip() if text.splitlines() else ""
    length = str(len(text))

    row = ensure_row(rows, fieldnames, code, no)
    if not row.get("タイトル"):
        row["タイトル"] = title
    content_rel = _to_repo_relative(content_path)
    row["台本"] = content_rel
    row["台本パス"] = content_rel
    row["文字数"] = length
    row.setdefault("進捗", "script_validated")

    status_path = data_dir / f"{no:03d}" / "status.json"
    status = read_status(status_path)
    md = status.get("metadata") if isinstance(status.get("metadata"), dict) else {}
    md.setdefault("assembled_path", content_rel)
    md.setdefault("assembled_characters", len(text))
    if title:
        md["title"] = title
        md["title_sanitized"] = title

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
    rows.sort(key=lambda r: int(r.get("No.") or 0))
    save_csv(csv_path, rows, fieldnames)


def main() -> None:
    for csv_path in CHANNELS_DIR.glob("*.csv"):
        if csv_path.name.lower().endswith("_planning_template.csv"):
            continue
        sync_channel(csv_path)


if __name__ == "__main__":
    main()

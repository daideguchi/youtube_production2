#!/usr/bin/env python3
"""
全チャンネルの台本（assembled.md）と planning CSV / status.json を同期するワンショット。
- progress/channels/*.csv を走査
- 対応する script_pipeline/data/{CH}/0xx/content/assembled.md があれば
  - CSV: タイトル（冒頭1行）、台本パス、文字数、進捗（未設定なら script_validated）を更新
  - status.json: title/assembled_path/assembled_characters 等を補完（既存ステージは保持）
"""

import csv
import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory_common.paths import planning_root, script_data_root

CHANNELS_DIR = planning_root() / "channels"
DATA_ROOT = script_data_root()


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
    row["台本"] = content_path.as_posix()
    row["台本パス"] = content_path.as_posix()
    row["文字数"] = length
    row.setdefault("進捗", "script_validated")

    status_path = data_dir / f"{no:03d}" / "status.json"
    status = read_status(status_path)
    md = status.get("metadata") if isinstance(status.get("metadata"), dict) else {}
    md.setdefault("assembled_path", content_path.as_posix())
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

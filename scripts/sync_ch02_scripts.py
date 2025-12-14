import csv
import json
from pathlib import Path

from factory_common.paths import script_data_root
from typing import Dict, List

CH_CODE = "CH02"
REPO_ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = REPO_ROOT / "progress" / "channels" / f"{CH_CODE}.csv"
DATA_DIR = script_data_root() / CH_CODE


def load_csv() -> List[Dict[str, str]]:
    if not CSV_PATH.exists():
        raise FileNotFoundError(f"CSV not found: {CSV_PATH}")
    with CSV_PATH.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or []
    return rows, fieldnames


def save_csv(rows: List[Dict[str, str]], fieldnames: List[str]) -> None:
    with CSV_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_status(path: Path) -> Dict:
    return json.loads(path.read_text()) if path.exists() else {}


def write_status(path: Path, payload: Dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


def ensure_row(rows: List[Dict[str, str]], fieldnames: List[str], no: int) -> Dict[str, str]:
    for r in rows:
        if r.get("No.") == str(no):
            return r
    # create empty row
    row = {key: "" for key in fieldnames}
    row["No."] = str(no)
    row["チャンネル"] = CH_CODE
    row["動画番号"] = str(no)
    row["動画ID"] = f"{CH_CODE}-{no:03d}"
    row["台本番号"] = f"{CH_CODE}-{no:03d}"
    rows.append(row)
    return row


def sync_one(no: int, rows: List[Dict[str, str]], fieldnames: List[str]) -> None:
    content_path = DATA_DIR / f"{no:03d}" / "content" / "assembled.md"
    if not content_path.exists():
        return
    text = content_path.read_text()
    title = text.splitlines()[0].strip() if text.splitlines() else ""
    length = str(len(text))
    row = ensure_row(rows, fieldnames, no)
    row["タイトル"] = row.get("タイトル") or title
    row["台本"] = content_path.as_posix()
    row["台本パス"] = content_path.as_posix()
    row["文字数"] = length
    row.setdefault("進捗", "script_validated")

    status_path = DATA_DIR / f"{no:03d}" / "status.json"
    status = read_status(status_path)
    md = status.get("metadata") if isinstance(status.get("metadata"), dict) else {}
    md.setdefault("assembled_path", content_path.as_posix())
    md.setdefault("assembled_characters", len(text))
    md["title"] = title if title else md.get("title", "")
    md["title_sanitized"] = md.get("title")
    status.setdefault("script_id", f"{CH_CODE}-{no:03d}")
    status.setdefault("channel", CH_CODE)
    status.setdefault("channel_code", CH_CODE)
    status.setdefault("video_number", f"{no:03d}")
    status.setdefault("status", "script_validated")
    status.setdefault("stages", {})
    status.setdefault("metadata", {})
    status["metadata"] = md
    write_status(status_path, status)


def main() -> None:
    rows, fieldnames = load_csv()
    # preserve column order; if missing expected columns, keep existing header
    for entry in sorted(DATA_DIR.iterdir()):
        if not entry.is_dir() or not entry.name.isdigit():
            continue
        no = int(entry.name)
        sync_one(no, rows, fieldnames)
    # sort rows by No.
    rows.sort(key=lambda r: int(r.get("No.") or 0))
    save_csv(rows, fieldnames)


if __name__ == "__main__":
    main()

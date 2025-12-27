import csv
import json
import sys
from pathlib import Path
from typing import Dict, List
import re

def _discover_repo_root(start: Path) -> Path:
    cur = start if start.is_dir() else start.parent
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate.resolve()
    return cur.resolve()


PROJECT_ROOT = _discover_repo_root(Path(__file__).resolve())
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from factory_common.paths import channels_csv_path, script_data_root

CH_CODE = "CH02"
CSV_PATH = channels_csv_path(CH_CODE)
DATA_DIR = script_data_root() / CH_CODE


def _to_repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except Exception:
        return path.as_posix()

def _is_probably_title_line(line: str) -> bool:
    # Legacy helper (kept for compatibility). This sync no longer infers titles from A-text.
    line = (line or "").strip()
    return bool(line) and line != "---" and len(line) <= 120


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
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def read_status(path: Path) -> Dict:
    return json.loads(path.read_text()) if path.exists() else {}


def write_status(path: Path, payload: Dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


def ensure_row(rows: List[Dict[str, str]], fieldnames: List[str], no: int) -> Dict[str, str]:
    # Planning CSV is the SoT. This sync must NOT create new planning rows.
    for r in rows:
        if r.get("No.") == str(no) or r.get("動画番号") == str(no):
            return r
    return {}


def sync_one(no: int, rows: List[Dict[str, str]], fieldnames: List[str]) -> None:
    base_dir = DATA_DIR / f"{no:03d}"
    content_path = base_dir / "content" / "assembled.md"
    if not content_path.exists():
        alt = base_dir / "content" / "assembled_human.md"
        if not alt.exists():
            return
        content_path = alt
    # Only sync into existing planning rows.
    row = ensure_row(rows, fieldnames, no)
    if not row:
        print(f"[WARN] {CH_CODE} {no:03d}: planning row not found; skip (no auto-create)", file=sys.stderr)
        return
    text = content_path.read_text(encoding="utf-8")
    char_count = _a_text_char_count(text)
    length = str(char_count)
    row_title = str(row.get("タイトル") or "").strip()

    status_path = base_dir / "status.json"
    status = read_status(status_path)
    md = status.get("metadata") if isinstance(status.get("metadata"), dict) else {}
    canonical_title = row_title

    content_rel = _to_repo_relative(base_dir / "content" / "assembled.md")
    row["台本"] = content_rel
    row["台本パス"] = content_rel
    row["文字数"] = length
    row.setdefault("進捗", "script_validated")

    md.setdefault("assembled_path", content_rel)
    md.setdefault("assembled_characters", char_count)
    if canonical_title:
        if str(md.get("sheet_title") or "").strip() != canonical_title:
            md["sheet_title"] = canonical_title
        if str(md.get("expected_title") or "").strip() != canonical_title:
            md["expected_title"] = canonical_title
        if str(md.get("title") or "").strip() != canonical_title:
            md["title"] = canonical_title
        if str(md.get("title_sanitized") or "").strip() != canonical_title:
            md["title_sanitized"] = canonical_title

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

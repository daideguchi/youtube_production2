from __future__ import annotations
import argparse
import json
from pathlib import Path
from typing import List, Dict, Any

DEFAULT_LOGS = [
    Path("logs/llm_usage.jsonl"),
    Path("logs/llm_context_analyzer.log"),
    Path("logs/tts_llm_usage.log"),
]

def load_tail(path: Path, limit: int) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    tail = lines[-limit:]
    out = []
    for line in tail:
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            out.append({"raw": line})
    return out


def main():
    ap = argparse.ArgumentParser(description="Show latest LLM log entries")
    ap.add_argument("--logs", nargs="*", type=Path, default=DEFAULT_LOGS, help="log files")
    ap.add_argument("--limit", type=int, default=5, help="tail N entries")
    args = ap.parse_args()

    report = {}
    for log in args.logs:
        report[str(log)] = load_tail(log, args.limit)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

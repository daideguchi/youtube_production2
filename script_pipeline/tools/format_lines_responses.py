"""
On-demand line formatter using LLMRouter (Azure Standard).
- Preserves content exactly (only inserts/replaces newlines).
- Ensures every line <= limit (default 27).
Usage:
  python -m script_pipeline.tools.format_lines_responses --input /path/to/file --output /path/to/out
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from factory_common.llm_router import get_router
from factory_common.paths import script_pkg_root

SYSTEM_PROMPT_PATH = script_pkg_root() / "prompts" / "format_lines_responses_system.txt"


def _strip_newlines(s: str) -> str:
    return s.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "")


def _all_lines_within_limit(s: str, limit: int) -> bool:
    soft_limit = limit + 2  # allow slight overrun (e.g., 29 when limit=27)
    return all(len(line) <= soft_limit for line in s.splitlines())


def _is_valid(orig: str, formatted: str, limit: int) -> bool:
    return _strip_newlines(orig) == _strip_newlines(formatted) and _all_lines_within_limit(formatted, limit)


def call_formatter(text: str, limit: int, retries: int = 2) -> str:
    # Fallback system prompt if file missing
    if SYSTEM_PROMPT_PATH.exists():
        system_prompt = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    else:
        system_prompt = "You are a formatter. Insert newlines to keep lines short. Do NOT change content."

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"次の日本語テキストを整形してください。\n\n【入力テキスト】\n{text}\n【入力テキストここまで】"},
    ]

    router = get_router()
    last_error = None

    for _ in range(retries + 1):
        try:
            # Use script_format task (standard tier)
            content = router.call(
                task="script_format",
                messages=messages,
                timeout=60
            )
            
            if content and _is_valid(text, content.strip(), limit):
                return content.strip()
            
            last_error = "validation_failed"
        except Exception as exc:
            last_error = str(exc)
            
    raise RuntimeError(f"Formatting failed: {last_error}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Format lines to <=27 chars.")
    ap.add_argument("--input", required=True, help="Input file path")
    ap.add_argument("--output", required=True, help="Output file path")
    ap.add_argument("--limit", type=int, default=27, help="Max chars per line")
    args = ap.parse_args()

    inp = Path(args.input)
    out = Path(args.output)
    
    if not inp.exists():
        sys.stderr.write(f"[ERROR] Input file not found: {inp}\n")
        sys.exit(1)
        
    text = inp.read_text(encoding="utf-8")

    try:
        formatted = call_formatter(text, args.limit)
    except Exception as exc:
        sys.stderr.write(f"[ERROR] {exc}\n")
        sys.exit(1)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(formatted + "\n", encoding="utf-8")
    print(f"wrote: {out}")


if __name__ == "__main__":
    main()

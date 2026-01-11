#!/usr/bin/env python3
"""
llm_hardcode_audit — guard against direct LLM calls outside central modules.

Policy:
- LLM HTTP endpoints / SDK calls must be routed via:
  - `packages/factory_common/llm_router.py` (text/chat + routing)
  - `packages/factory_common/image_client.py` (image generation)
  - `packages/factory_common/llm_client.py` (legacy client wrapper)

This audit is intentionally lightweight and conservative: it flags obvious
hardcoded endpoints and direct OpenAI SDK invocations in Python product code.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from _bootstrap import bootstrap

REPO_ROOT = bootstrap(load_env=False)


@dataclass(frozen=True)
class Finding:
    path: Path
    line_no: int
    needle: str
    line: str


ALLOWED_FILES = {
    Path("packages/factory_common/llm_router.py"),
    Path("packages/factory_common/llm_client.py"),
    Path("packages/factory_common/image_client.py"),
}

NEEDLES = [
    # Hardcoded endpoints (product code should not embed these).
    "openrouter.ai/api/v1/chat/completions",
    "api.openai.com/v1/chat/completions",
    "api.openai.com/v1/responses",
    "api.anthropic.com/v1/messages",
    # Direct SDK usage (should live in LLMRouter/LLMClient only).
    ".chat.completions.create(",
    ".responses.create(",
]


def _iter_python_files() -> list[Path]:
    roots = [REPO_ROOT / "packages", REPO_ROOT / "apps"]
    files: list[Path] = []
    for r in roots:
        if not r.exists():
            continue
        files.extend(sorted(p for p in r.rglob("*.py") if p.is_file()))
    return files


def _scan_file(path: Path) -> list[Finding]:
    rel = path.relative_to(REPO_ROOT)
    if rel in ALLOWED_FILES:
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return []
    findings: list[Finding] = []
    for i, line in enumerate(text.splitlines(), 1):
        for needle in NEEDLES:
            if needle in line:
                findings.append(Finding(path=rel, line_no=i, needle=needle, line=line.rstrip()))
    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Detect hardcoded direct LLM calls outside central modules.")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    findings: list[Finding] = []
    for p in _iter_python_files():
        findings.extend(_scan_file(p))

    if not findings:
        if args.verbose:
            print("[ok] no direct LLM hardcodes found")
        return 0

    print("[FAIL] direct LLM hardcodes detected (route via LLMRouter/ImageClient):")
    for f in findings:
        print(f"- {f.path}:{f.line_no}: needle={f.needle!r} line={f.line}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())


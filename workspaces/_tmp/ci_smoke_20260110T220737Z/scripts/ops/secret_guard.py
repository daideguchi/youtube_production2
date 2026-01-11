#!/usr/bin/env python3
"""
secret_guard.py — prevent accidental commits of API keys / tokens.

This script scans *tracked* files (git ls-files) for known secret patterns and
fails with a non-zero exit code if any are found.

Important:
- It does NOT print the raw matched secret values (output is masked).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import re


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str
    preview: str


PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # OpenRouter API key (example: sk-or-v1-<64 hex>)
    ("openrouter_api_key", re.compile(r"sk-or-v1-[0-9a-f]{64}", re.IGNORECASE)),
    # Google API key (example: AIzaSy....)
    ("google_api_key", re.compile(r"AIzaSy[0-9A-Za-z_-]{30,}")),
    # GitHub classic token
    ("github_token_classic", re.compile(r"ghp_[0-9A-Za-z]{30,}")),
    # GitHub fine-grained token
    ("github_token_fine_grained", re.compile(r"github_pat_[0-9A-Za-z_]{20,}")),
]


def _run_git(args: list[str]) -> str:
    out = subprocess.check_output(["git", *args], stderr=subprocess.STDOUT)
    return out.decode("utf-8", errors="replace")


def _tracked_files(repo_root: Path) -> list[Path]:
    raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=repo_root)
    parts = [p for p in raw.split(b"\x00") if p]
    return [repo_root / p.decode("utf-8", errors="strict") for p in parts]


def _looks_binary(data: bytes) -> bool:
    return b"\x00" in data


def _mask_line(line: str) -> str:
    masked = line
    for _name, rx in PATTERNS:
        masked = rx.sub("***REDACTED***", masked)
    return masked.rstrip("\n")


def _scan_file(path: Path, repo_root: Path, max_bytes: int) -> Iterable[Finding]:
    try:
        if not path.is_file():
            return []
        if path.stat().st_size > max_bytes:
            return []
        data = path.read_bytes()
    except Exception:
        return []

    if _looks_binary(data):
        return []

    text = data.decode("utf-8", errors="replace")
    findings: list[Finding] = []
    for kind, rx in PATTERNS:
        for m in rx.finditer(text):
            line_no = text.count("\n", 0, m.start()) + 1
            line_start = text.rfind("\n", 0, m.start()) + 1
            line_end = text.find("\n", m.start())
            if line_end == -1:
                line_end = len(text)
            line = text[line_start:line_end]
            rel = path.relative_to(repo_root).as_posix()
            findings.append(Finding(path=rel, line=line_no, kind=kind, preview=_mask_line(line)))
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description="Fail if tracked files contain leaked-looking secrets (masked output).")
    ap.add_argument("--max-bytes", type=int, default=2_000_000, help="Skip files larger than this many bytes.")
    ap.add_argument("--paths-from-git-diff-base", type=str, default="", help="If set, scan only files changed since <ref>.")
    args = ap.parse_args()

    repo_root = Path(_run_git(["rev-parse", "--show-toplevel"]).strip())

    if args.paths_from_git_diff_base:
        base = args.paths_from_git_diff_base
        raw = subprocess.check_output(
            ["git", "diff", "--name-only", "--diff-filter=ACMRTUXB", f"{base}...HEAD"], cwd=repo_root
        ).decode("utf-8", errors="replace")
        rels = [r.strip() for r in raw.splitlines() if r.strip()]
        paths = [repo_root / r for r in rels]
    else:
        paths = _tracked_files(repo_root)

    all_findings: list[Finding] = []
    for p in paths:
        all_findings.extend(list(_scan_file(p, repo_root=repo_root, max_bytes=args.max_bytes)))

    if not all_findings:
        print("[secret_guard] OK: no secret-like patterns found.")
        return 0

    print("[secret_guard] FOUND potential secrets (values masked):", file=sys.stderr)
    for f in sorted(all_findings, key=lambda x: (x.path, x.line, x.kind)):
        print(f"- {f.path}:{f.line} [{f.kind}] {f.preview}", file=sys.stderr)
    print("", file=sys.stderr)
    print("Action: revoke/rotate the compromised keys and remove them from the repo.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())


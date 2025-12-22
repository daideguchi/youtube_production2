#!/usr/bin/env python3
"""Audit channel prompts and canonical script surfaces for legacy delimiters / punctuation.

This tool is used in health checks, so it should be:
- fast (avoid scanning large, irrelevant trees)
- low-noise (skip logs/analysis artifacts by default)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Sequence

try:
    import portalocker  # type: ignore
except ImportError:  # pragma: no cover
    portalocker = None  # type: ignore

from _bootstrap import bootstrap

REPO_ROOT = bootstrap()

from factory_common.paths import logs_root, script_data_root, script_pkg_root

SCRIPT_ROOT = script_pkg_root()
CHANNELS_DIR = SCRIPT_ROOT / "channels"
DATA_DIR = script_data_root()
LOG_DIR = logs_root() / "regression"

@dataclass(frozen=True)
class Pattern:
    name: str
    regex: re.Pattern[str]
    description: str
    fixer: Callable[[str], str] | None = None


def _pattern_replace(pattern: re.Pattern[str], replacement: str) -> Callable[[str], str]:
    def _replace(text: str) -> str:
        return pattern.sub(replacement, text)

    return _replace


PATTERNS: Sequence[Pattern] = (
    Pattern(
        name="triple_slash",
        regex=re.compile(r"///"),
        description="Legacy delimiter '///' detected",
        fixer=_pattern_replace(re.compile(r"///"), ""),
    ),
    Pattern(
        name="double_japanese_period",
        regex=re.compile(r"。。+"),
        description="Repeated Japanese full stop (。。)",
        fixer=_pattern_replace(re.compile(r"。。+"), "。"),
    ),
    Pattern(
        name="jp_period_ascii_period",
        regex=re.compile(r"。\."),
        description="Japanese period immediately followed by ASCII period",
        fixer=_pattern_replace(re.compile(r"。\."), "。"),
    ),
)


def iter_channel_dirs(channel_code: str | None) -> Iterable[Path]:
    if not CHANNELS_DIR.exists():
        return []
    for child in CHANNELS_DIR.iterdir():
        if not child.is_dir():
            continue
        if not child.name.startswith("CH"):
            continue
        if channel_code and not child.name.startswith(channel_code.upper()):
            continue
        yield child


def iter_prompt_files(channel_code: str | None) -> List[Path]:
    files: List[Path] = []
    for ch_dir in iter_channel_dirs(channel_code):
        for candidate in ("script_prompt.txt", "script_guidelines.md", "persona.md"):
            path = ch_dir / candidate
            if path.exists():
                files.append(path)
    return files


def iter_script_files(channel_code: str | None) -> Iterable[Path]:
    if not DATA_DIR.exists():
        return []
    for ch_dir in DATA_DIR.iterdir():
        if not ch_dir.is_dir():
            continue
        if channel_code and ch_dir.name.upper() != channel_code.upper():
            continue
        for path in ch_dir.rglob("*"):
            if not path.is_file():
                continue

            # Canonical A-text surfaces only (skip logs/analysis/aux files).
            if path.name in {"assembled.md", "assembled_human.md"}:
                if "content" in path.parts and path.parent.name in {"content", "final"}:
                    yield path
                continue

            if path.suffix.lower() == ".txt" and path.parent.name == "audio_prep":
                if path.name.startswith("script_sanitized"):
                    yield path
                continue


def _load_registry_prompt_paths() -> List[Path]:
    """
    Use backend prompt registry (if importable) to collect prompt paths.
    """
    try:
        from ui.backend.main import _load_prompt_documents  # type: ignore
    except Exception as exc:  # noqa: BLE001
        print(f"[registry] skip (import failed): {exc}")
        return []
    specs = _load_prompt_documents()
    paths: List[Path] = []
    for spec in specs.values():
        for raw in [spec.get("primary_path"), *spec.get("sync_paths", [])]:
            if not raw:
                continue
            path = Path(raw)
            if path.exists() and path.is_file():
                paths.append(path)
    return paths


def scan_file(path: Path, apply: bool) -> dict:
    text = path.read_text(encoding="utf-8", errors="ignore")
    issues = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for pattern in PATTERNS:
            for match in pattern.regex.finditer(line):
                issues.append(
                    {
                        "pattern": pattern.name,
                        "description": pattern.description,
                        "line": line_no,
                        "column": match.start() + 1,
                        "snippet": line.strip(),
                    }
                )
    changed = False
    if apply and issues:
        new_text = text
        for pattern in PATTERNS:
            if any(issue["pattern"] == pattern.name for issue in issues) and pattern.fixer:
                new_text = pattern.fixer(new_text)
        if new_text != text:
            changed = True
            if portalocker:
                with portalocker.Lock(str(path), "w", timeout=5) as fh:
                    fh.write(new_text)
            else:  # pragma: no cover
                path.write_text(new_text, encoding="utf-8")
    return {"file": str(path.relative_to(REPO_ROOT)), "issues": issues, "changed": changed}


def write_log(results: List[dict], apply: bool) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = LOG_DIR / f"prompt_audit_{timestamp}.log"
    payload = {
        "timestamp": timestamp,
        "apply": apply,
        "issue_count": sum(len(entry["issues"]) for entry in results),
        "files": results,
    }
    log_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return log_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit prompts and scripts for delimiter/punctuation issues")
    parser.add_argument("--channel", help="Target channel code (e.g., CH06)")
    parser.add_argument("--apply", action="store_true", help="Apply safe automatic fixes in-place")
    parser.add_argument(
        "--skip-scripts",
        action="store_true",
        help="Only audit prompts/guidelines (skip data/ scripts repository)",
    )
    parser.add_argument(
        "--no-registry",
        action="store_true",
        help="Do not use backend prompt registry; fallback to filesystem heuristics only",
    )
    args = parser.parse_args()

    target_channel = args.channel.upper() if args.channel else None
    files: List[Path] = []
    if not args.no_registry:
        registry_paths = _load_registry_prompt_paths()
        if registry_paths:
            print(f"[registry] using {len(registry_paths)} prompt paths")
            files.extend(registry_paths)
        else:
            print("[registry] no entries; fallback to filesystem scan")
    files.extend(iter_prompt_files(target_channel))
    if not args.skip_scripts:
        files.extend(iter_script_files(target_channel))

    # Dedupe (registry + heuristics can overlap).
    seen: set[Path] = set()
    deduped: List[Path] = []
    for path in files:
        key = path.resolve() if path.exists() else path
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    files = deduped

    if not files:
        raise SystemExit("No files matched the criteria.")

    results: List[dict] = []
    issue_total = 0
    for path in files:
        entry = scan_file(path, apply=args.apply)
        if entry["issues"]:
            issue_total += len(entry["issues"])
        results.append(entry)

    log_path = write_log(results, apply=args.apply)

    print(f"Scanned {len(files)} files; found {issue_total} issues. Log: {log_path.relative_to(REPO_ROOT)}")
    if args.apply:
        changed_files = [r["file"] for r in results if r["changed"]]
        if changed_files:
            print("Updated files:")
            for file in changed_files:
                print(f"  - {file}")

    if issue_total and not args.apply:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

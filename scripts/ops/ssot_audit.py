#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


from _bootstrap import bootstrap

REPO_ROOT = bootstrap(load_env=False)
SSOT_ROOT = REPO_ROOT / "ssot"


@dataclass(frozen=True)
class SsotAuditReport:
    created_at: str
    ssot_root: str
    counts: dict
    problems: dict


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _extract_backticks(text: str) -> list[str]:
    # NOTE: very simple markdown parsing by design.
    return re.findall(r"`([^`]+)`", text or "")


def _normalize_ssot_rel(p: str) -> str:
    raw = (p or "").strip().replace("\\", "/")
    raw = raw.removeprefix("ssot/").lstrip("/")
    return raw


def _looks_like_ssot_file(rel: str) -> bool:
    rel = (rel or "").strip()
    if not rel:
        return False
    if rel.startswith("http://") or rel.startswith("https://"):
        return False
    return any(rel.endswith(ext) for ext in (".md", ".json", ".txt", ".yaml", ".yml"))


def _looks_like_pattern(rel: str) -> bool:
    # Examples in docs like `PLAN_*.md` or `PLAN_<DOMAIN>_<TOPIC>.md`
    return any(ch in (rel or "") for ch in ("*", "<", ">"))


def _is_ssot_local_reference(rel: str) -> bool:
    """
    DOCS_INDEX/PLAN_STATUS contain backticked paths for commands and repo files.
    For auditing the SSOT indices, we only care about references that should live
    under `ssot/` (root files or well-known SSOT subdirs).
    """
    rel = (rel or "").strip().replace("\\", "/")
    if not _looks_like_ssot_file(rel):
        return False
    if _looks_like_pattern(rel):
        return False
    if rel.startswith(("history/", "handoffs/", "agent_runbooks/", "completed/", "ops/", "plans/", "reference/")):
        return True
    return "/" not in rel


def _existing_ssot_files() -> set[str]:
    out: set[str] = set()
    if not SSOT_ROOT.exists():
        return out
    for fp in SSOT_ROOT.rglob("*"):
        if not fp.is_file():
            continue
        rel = fp.relative_to(SSOT_ROOT).as_posix()
        out.add(rel)
    return out


def _index_paths_from_markdown(path: Path) -> set[str]:
    raw = _read_text(path)
    out: set[str] = set()
    for token in _extract_backticks(raw):
        rel = _normalize_ssot_rel(token)
        if _is_ssot_local_reference(rel):
            out.add(rel)
    return out


def _subset(paths: Iterable[str], *, prefix: str) -> set[str]:
    pre = (prefix or "").strip().rstrip("/") + "/"
    return {p for p in paths if p.startswith(pre)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit SSOT index/plan listing consistency (read-only).")
    ap.add_argument("--json", action="store_true", help="Print JSON report to stdout.")
    ap.add_argument("--write", action="store_true", help="Write JSON report under workspaces/logs/ssot/.")
    ap.add_argument("--strict", action="store_true", help="Also require completed/*.md to be indexed in DOCS_INDEX.")
    args = ap.parse_args()

    ssot_files = _existing_ssot_files()
    docs_index = _index_paths_from_markdown(SSOT_ROOT / "DOCS_INDEX.md")
    plan_status = _index_paths_from_markdown(SSOT_ROOT / "plans" / "PLAN_STATUS.md")

    # Required: SSOT root docs + ops/plans/reference should appear in DOCS_INDEX.
    required_root_md = {p.name for p in SSOT_ROOT.glob("*.md") if p.is_file() and p.name != "DOCS_INDEX.md"}
    required_ops = {
        p.relative_to(SSOT_ROOT).as_posix()
        for p in (SSOT_ROOT / "ops").glob("*")
        if p.is_file() and p.suffix in (".md", ".json", ".yaml", ".yml", ".txt")
    }
    required_plans = {
        p.relative_to(SSOT_ROOT).as_posix()
        for p in (SSOT_ROOT / "plans").glob("*.md")
        if p.is_file()
    }
    required_reference = {
        p.relative_to(SSOT_ROOT).as_posix()
        for p in (SSOT_ROOT / "reference").glob("*.md")
        if p.is_file()
    }
    required_docs_index = required_root_md | required_ops | required_plans | required_reference
    docs_index_missing_top_level = sorted(required_docs_index - docs_index)
    docs_index_missing_completed = sorted(_subset(ssot_files, prefix="completed") - docs_index) if args.strict else []

    docs_index_listed_missing_files = sorted([p for p in docs_index if p not in ssot_files])

    # Plans: plans/PLAN_*.md should be listed in plans/PLAN_STATUS.md.
    plan_files_root = {
        p.relative_to(SSOT_ROOT).as_posix()
        for p in (SSOT_ROOT / "plans").glob("PLAN_*.md")
        if p.is_file() and p.name != "PLAN_STATUS.md"
    }
    plan_status_missing = sorted(plan_files_root - plan_status)
    plan_status_listed_missing_files = sorted([p for p in plan_status if p not in ssot_files])

    problems = {
        "docs_index_missing_top_level": docs_index_missing_top_level,
        "docs_index_missing_completed": docs_index_missing_completed,
        "docs_index_listed_missing_files": docs_index_listed_missing_files,
        "plan_status_missing": plan_status_missing,
        "plan_status_listed_missing_files": plan_status_listed_missing_files,
    }

    counts = {
        "ssot_files_total": len(ssot_files),
        "docs_index_listed": len(docs_index),
        "plan_status_listed": len(plan_status),
        "top_level_md_total": len(required_docs_index),
        "plan_root_total": len(plan_files_root),
        "problems_total": sum(len(v) for v in problems.values()),
    }

    report = SsotAuditReport(
        created_at=_now_iso_utc(),
        ssot_root=str(SSOT_ROOT),
        counts=counts,
        problems=problems,
    )

    if args.json:
        print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    else:
        print(f"[ssot_audit] ssot_files={counts['ssot_files_total']} problems={counts['problems_total']}")
        if docs_index_missing_top_level:
            print("missing in DOCS_INDEX (top-level):")
            for p in docs_index_missing_top_level:
                print(f"  - {p}")
        if args.strict and docs_index_missing_completed:
            print("missing in DOCS_INDEX (completed/*):")
            for p in docs_index_missing_completed[:50]:
                print(f"  - {p}")
            if len(docs_index_missing_completed) > 50:
                print(f"  ... ({len(docs_index_missing_completed)-50} more)")
        if docs_index_listed_missing_files:
            print("DOCS_INDEX lists missing files:")
            for p in docs_index_listed_missing_files:
                print(f"  - {p}")
        if plan_status_missing:
            print("missing in PLAN_STATUS:")
            for p in plan_status_missing:
                print(f"  - {p}")
        if plan_status_listed_missing_files:
            print("PLAN_STATUS lists missing files:")
            for p in plan_status_listed_missing_files:
                print(f"  - {p}")

    if args.write:
        out_dir = REPO_ROOT / "workspaces" / "logs" / "ssot"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"ssot_audit_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        out_path.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if not args.json:
            print(f"[ssot_audit] wrote {out_path.relative_to(REPO_ROOT)}")

    return 0 if counts["problems_total"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

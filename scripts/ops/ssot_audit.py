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

_TEXT_AUDIT_SCOPES = ("core", "all")

# Core docs where ambiguous language must not appear.
# (Operator-facing; must be deterministic.)
_TEXT_AUDIT_CORE_DOCS = (
    "START_HERE.md",
    "ssot/SSOT_COMPASS.md",
    "ssot/OPS_SYSTEM_OVERVIEW.md",
    "ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md",
    "ssot/ops/OPS_ENTRYPOINTS_INDEX.md",
    "ssot/ops/OPS_EXECUTION_PATTERNS.md",
    "ssot/ops/OPS_FIXED_RECOVERY_COMMANDS.md",
    "ssot/ops/OPS_LOGGING_MAP.md",
)

# SSOT must avoid terms that require reader interpretation.
# Use SSOT_COMPASS vocabulary (固定/既定/オプション/禁止/補助) instead.
_AMBIGUOUS_TERMS_CORE = (
    "推奨",
    "任意",
    "非推奨",
    "おすすめ",
    "原則",
    "基本的に",
    "なるべく",
    "できるだけ",
    "可能なら",
    "必要なら",
    "場合によって",
    "目安",
    "望ましい",
    "ベストエフォート",
    "best-effort",
    "best effort",
)

_REPO_FILE_RE = re.compile(
    r"(?:^|[^A-Za-z0-9_./\\-])"
    r"(?P<path>(?:\\./)?(?:ssot|scripts|packages|apps|prompts|configs)/"
    r"[A-Za-z0-9_./\\-]+\.(?:md|py|sh|jsonl|json|yaml|yml|txt|tsx|ts|jsx|js|css))"
)

_LOCAL_ONLY_CONFIG_PATHS = {
    # Secrets / local-only configs (not tracked; expected to be absent in CI).
    "configs/drive_oauth_client.json",
}


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
    raw = raw.removeprefix("./")
    raw = raw.lstrip("/")
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


def _iter_ssot_markdown_files(
    *,
    include_history: bool,
    include_completed: bool,
    include_cleanup_log: bool,
) -> list[Path]:
    out: list[Path] = []
    if not SSOT_ROOT.exists():
        return out
    for fp in SSOT_ROOT.rglob("*.md"):
        if not fp.is_file():
            continue
        rel = fp.relative_to(SSOT_ROOT).as_posix()
        if rel == "ops/OPS_CLEANUP_EXECUTION_LOG.md" and not include_cleanup_log:
            continue
        top = rel.split("/", 1)[0]
        if top == "history" and not include_history:
            continue
        if top == "completed" and not include_completed:
            continue
        out.append(fp)
    return sorted(out)


def _is_in_audit_scope(
    rel: str,
    *,
    include_history: bool,
    include_completed: bool,
    include_cleanup_log: bool,
) -> bool:
    rel = (rel or "").strip().replace("\\", "/")
    if not rel:
        return False
    if rel == "ops/OPS_CLEANUP_EXECUTION_LOG.md" and not include_cleanup_log:
        return False
    top = rel.split("/", 1)[0]
    if top == "history" and not include_history:
        return False
    if top == "completed" and not include_completed:
        return False
    return True


def _extract_markdown_link_targets(line: str) -> list[str]:
    # NOTE: intentionally simplistic markdown parsing.
    return re.findall(r"\[[^\]]+\]\(([^)]+)\)", line or "")


def _extract_repo_file_paths(fragment: str) -> set[str]:
    out: set[str] = set()
    for m in _REPO_FILE_RE.finditer(fragment or ""):
        p = m.group("path").strip().replace("\\", "/").lstrip("./")
        # Strip common trailing punctuation from inline docs.
        p = p.rstrip(").,;:\"'")
        # Skip obvious template placeholders used throughout SSOT.
        if any(tok in p for tok in ("CHxx", "NNN", "...")):
            continue
        out.add(p)
    return out


def _is_ignored_missing_repo_path(repo_path: str) -> bool:
    p = (repo_path or "").strip().replace("\\", "/").lstrip("./")
    if not p:
        return True
    if p in _LOCAL_ONLY_CONFIG_PATHS:
        return True
    if p.startswith("configs/") and ".local." in p:
        return True
    return False


def _audit_repo_paths_from_ssot_docs(
    *,
    docs: Iterable[Path],
) -> tuple[dict[str, str], dict[str, set[str]]]:
    """
    Returns:
      - missing_repo_paths: map[path -> example "ssot/..:line"]
      - ssot_edges: map[src_rel -> set[dst_rel]] for SSOT-local references
    """
    ssot_files = _existing_ssot_files()
    missing_repo_paths: dict[str, str] = {}
    ssot_edges: dict[str, set[str]] = {}

    for fp in docs:
        src_rel = fp.relative_to(SSOT_ROOT).as_posix()
        ssot_edges.setdefault(src_rel, set())

        raw = _read_text(fp)
        for line_no, line in enumerate(raw.splitlines(), start=1):
            # 1) backticks: may include commands containing paths
            for token in re.findall(r"`([^`]+)`", line):
                for repo_path in _extract_repo_file_paths(token):
                    if _is_ignored_missing_repo_path(repo_path):
                        continue
                    if repo_path not in missing_repo_paths and not (REPO_ROOT / repo_path).exists():
                        missing_repo_paths[repo_path] = f"ssot/{src_rel}:{line_no}"
                ssot_ref = _normalize_ssot_rel(token)
                if _is_ssot_local_reference(ssot_ref) and ssot_ref != src_rel:
                    ssot_edges[src_rel].add(ssot_ref)

            # 2) markdown links: e.g. [text](ssot/ops/OPS_*.md)
            for target in _extract_markdown_link_targets(line):
                for repo_path in _extract_repo_file_paths(target):
                    if _is_ignored_missing_repo_path(repo_path):
                        continue
                    if repo_path not in missing_repo_paths and not (REPO_ROOT / repo_path).exists():
                        missing_repo_paths[repo_path] = f"ssot/{src_rel}:{line_no}"
                ssot_ref = _normalize_ssot_rel(target)
                if _is_ssot_local_reference(ssot_ref) and ssot_ref != src_rel:
                    ssot_edges[src_rel].add(ssot_ref)

    # Remove edges pointing outside SSOT (keep only existing nodes for downstream stats).
    for src, dsts in list(ssot_edges.items()):
        ssot_edges[src] = {d for d in dsts if d in ssot_files}

    return missing_repo_paths, ssot_edges


def _audit_text_invariants(*, paths: Iterable[Path]) -> dict[str, list[str]]:
    """
    SSOT text invariants:
      - no ambiguous terms in operator-facing docs (core)
    """
    missing: list[str] = []
    ambiguous_hits: list[str] = []

    for p in paths:
        rel = p.relative_to(REPO_ROOT).as_posix() if p.is_absolute() else str(p)
        if not p.exists():
            missing.append(rel)
            continue
        raw = _read_text(p)
        for line_no, line in enumerate(raw.splitlines(), start=1):
            for term in _AMBIGUOUS_TERMS_CORE:
                if term in line:
                    snippet = (line or "").strip()
                    if len(snippet) > 140:
                        snippet = snippet[:140] + "..."
                    ambiguous_hits.append(f"{rel}:{line_no} | term={term} | {snippet}")

    out: dict[str, list[str]] = {}
    if missing:
        out["text_audit_missing_files"] = sorted(missing)
    if ambiguous_hits:
        out["text_audit_ambiguous_terms"] = sorted(ambiguous_hits)
    return out


def _subset(paths: Iterable[str], *, prefix: str) -> set[str]:
    pre = (prefix or "").strip().rstrip("/") + "/"
    return {p for p in paths if p.startswith(pre)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit SSOT index/plan listing consistency (read-only).")
    ap.add_argument("--json", action="store_true", help="Print JSON report to stdout.")
    ap.add_argument("--write", action="store_true", help="Write JSON report under workspaces/logs/ssot/.")
    ap.add_argument("--strict", action="store_true", help="Also require completed/*.md to be indexed in DOCS_INDEX.")
    ap.add_argument(
        "--path-audit",
        action="store_true",
        help="Also audit repo file paths referenced from SSOT docs (best-effort; ignores patterns).",
    )
    ap.add_argument(
        "--link-audit",
        action="store_true",
        help="Also audit SSOT markdown links (reports docs referenced only from DOCS_INDEX/PLAN_STATUS).",
    )
    ap.add_argument(
        "--text-audit",
        action="store_true",
        help="Also audit SSOT text invariants (no ambiguous terms in core docs).",
    )
    ap.add_argument(
        "--text-scope",
        choices=_TEXT_AUDIT_SCOPES,
        default="core",
        help="Scope for --text-audit (default: core).",
    )
    ap.add_argument("--include-history", action="store_true", help="Include ssot/history/* in --path-audit/--link-audit.")
    ap.add_argument(
        "--include-completed",
        action="store_true",
        help="Include ssot/completed/* in --path-audit/--link-audit (historical; may contain legacy paths).",
    )
    ap.add_argument(
        "--include-cleanup-log",
        action="store_true",
        help="Include ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md in --path-audit/--link-audit (contains deleted paths).",
    )
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

    if args.text_audit:
        if args.text_scope == "core":
            text_paths = [REPO_ROOT / p for p in _TEXT_AUDIT_CORE_DOCS]
        else:
            md_docs_text = _iter_ssot_markdown_files(
                include_history=args.include_history,
                include_completed=args.include_completed,
                include_cleanup_log=args.include_cleanup_log,
            )
            text_paths = [REPO_ROOT / "START_HERE.md", *md_docs_text]
        problems.update(_audit_text_invariants(paths=text_paths))

    if args.path_audit or args.link_audit:
        md_docs = _iter_ssot_markdown_files(
            include_history=args.include_history,
            include_completed=args.include_completed,
            include_cleanup_log=args.include_cleanup_log,
        )
        missing_repo_paths, ssot_edges = _audit_repo_paths_from_ssot_docs(docs=md_docs)

        if args.path_audit:
            problems["broken_repo_paths"] = [f"{p} | {missing_repo_paths[p]}" for p in sorted(missing_repo_paths.keys())]
        if args.link_audit:
            index_docs = {"DOCS_INDEX.md", "plans/PLAN_STATUS.md"}
            ssot_nodes = {
                p
                for p in ssot_files
                if p in index_docs
                or (
                    _looks_like_ssot_file(p)
                    and _is_in_audit_scope(
                        p,
                        include_history=args.include_history,
                        include_completed=args.include_completed,
                        include_cleanup_log=args.include_cleanup_log,
                    )
                )
            }
            incoming_all: dict[str, int] = {p: 0 for p in ssot_nodes}
            incoming_non_index: dict[str, int] = {p: 0 for p in ssot_nodes}

            for src, dsts in ssot_edges.items():
                for dst in dsts:
                    if dst not in incoming_all:
                        continue
                    incoming_all[dst] += 1
                    if src not in index_docs:
                        incoming_non_index[dst] += 1

            # Candidate "noise": only referenced from indexes, not from other SSOT docs.
            isolated = sorted([p for p, n in incoming_non_index.items() if n == 0 and p not in index_docs])
            problems["ssot_isolated_docs"] = isolated

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
        if args.path_audit and problems.get("broken_repo_paths"):
            broken = problems["broken_repo_paths"]
            print("broken repo paths referenced from SSOT:")
            for p in broken[:50]:
                print(f"  - {p}")
            if len(broken) > 50:
                print(f"  ... ({len(broken)-50} more)")
        if args.link_audit and problems.get("ssot_isolated_docs"):
            isolated = problems["ssot_isolated_docs"]
            print("SSOT isolated docs (no incoming refs except indexes):")
            for p in isolated[:50]:
                print(f"  - {p}")
            if len(isolated) > 50:
                print(f"  ... ({len(isolated)-50} more)")
        if args.text_audit and problems.get("text_audit_missing_files"):
            items = problems["text_audit_missing_files"]
            print("text audit missing files:")
            for p in items[:50]:
                print(f"  - {p}")
            if len(items) > 50:
                print(f"  ... ({len(items)-50} more)")
        if args.text_audit and problems.get("text_audit_ambiguous_terms"):
            items = problems["text_audit_ambiguous_terms"]
            print("text audit ambiguous terms:")
            for p in items[:50]:
                print(f"  - {p}")
            if len(items) > 50:
                print(f"  ... ({len(items)-50} more)")

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

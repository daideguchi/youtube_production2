#!/usr/bin/env python3
"""
docs_inventory.py — ドキュメント棚卸し（参照ゼロ候補を抽出）

目的:
  - SSOT以外の docs（apps/packages/prompts 等）が、どこから参照されているかを可視化する
  - 「参照ゼロ（Docs参照ゼロ）」の候補を機械的に列挙し、SSOT更新→archive-first削除の材料にする

注:
  - SSOT内の索引整合は `python3 scripts/ops/ssot_audit.py --strict` が正本
  - 本ツールは「非SSOT docs」を主対象にする（探索ノイズ除去用）

Usage:
  python3 scripts/ops/docs_inventory.py --stdout
  python3 scripts/ops/docs_inventory.py --write
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from _bootstrap import bootstrap


@dataclass(frozen=True)
class DocItem:
    path: str
    refs_in_ssot: int
    example: str | None


def _now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _run_rg(*, repo_root: Path, pattern: str, roots: Sequence[str]) -> list[str]:
    cmd = ["rg", "-n", "--no-heading", "--fixed-strings", pattern, *roots]
    proc = subprocess.run(
        cmd,
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    if proc.returncode not in (0, 1):
        raise RuntimeError(proc.stderr.strip() or "rg failed")
    return [line for line in proc.stdout.splitlines() if line.strip()]


def _count_refs(*, repo_root: Path, rel: str, roots: Sequence[str]) -> tuple[int, str | None]:
    hits = _run_rg(repo_root=repo_root, pattern=rel, roots=roots)
    filtered: list[str] = []
    for line in hits:
        try:
            file_part, line_no, _ = line.split(":", 2)
        except ValueError:
            continue
        file_part = file_part.lstrip("./")
        if file_part == rel:
            continue
        filtered.append(f"{file_part}:{line_no}")
    filtered.sort()
    return len(filtered), (filtered[0] if filtered else None)


def _iter_docs(repo_root: Path) -> list[str]:
    out: list[str] = []
    roots = [repo_root / "apps", repo_root / "packages", repo_root / "prompts"]
    for root in roots:
        if not root.exists():
            continue
        for fp in root.rglob("*.md"):
            if not fp.is_file():
                continue
            if "node_modules" in fp.parts:
                continue
            rel = fp.relative_to(repo_root).as_posix()
            out.append(rel)
    # top-level docs (README 等)
    for name in ["README.md", "START_HERE.md", "DECLARATION.txt"]:
        p = repo_root / name
        if p.exists() and p.is_file():
            out.append(p.relative_to(repo_root).as_posix())
    return sorted(set(out))


def _render(items: list[DocItem]) -> str:
    lines: list[str] = []
    lines.append("# DOCS_INVENTORY — 非SSOT docs 棚卸し（自動生成）")
    lines.append("")
    lines.append("| doc | refs_in_ssot | example |")
    lines.append("| --- | ---: | --- |")
    for item in items:
        lines.append(f"| `{item.path}` | {item.refs_in_ssot} | {item.example or '—'} |")
    lines.append("")
    zeros = [item.path for item in items if item.refs_in_ssot == 0]
    if zeros:
        lines.append("## refs_in_ssot=0（SSOTから未参照）")
        lines.append("")
        for p in zeros:
            lines.append(f"- `{p}`")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Inventory non-SSOT docs and their references from ssot/**.")
    ap.add_argument("--max-docs", type=int, default=400, help="safety limit")
    ap.add_argument("--write", action="store_true", help="Write report under workspaces/logs/regression/docs_inventory/")
    ap.add_argument("--stdout", action="store_true", help="Print report to stdout (default)")
    args = ap.parse_args()

    repo_root = bootstrap(load_env=False)
    docs = _iter_docs(repo_root)
    if len(docs) > args.max_docs:
        print(f"[docs_inventory] too many docs: {len(docs)} > {args.max_docs}", file=sys.stderr)
        return 2

    ssot_roots = ["ssot", "README.md", "START_HERE.md"]
    items: list[DocItem] = []
    for rel in docs:
        refs, ex = _count_refs(repo_root=repo_root, rel=rel, roots=ssot_roots)
        items.append(DocItem(path=rel, refs_in_ssot=refs, example=ex))

    items.sort(key=lambda x: (x.refs_in_ssot, x.path))
    report = _render(items)

    if args.write:
        out_dir = repo_root / "workspaces" / "logs" / "regression" / "docs_inventory"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"docs_inventory_{_now_compact()}.md"
        out_path.write_text(report + "\n", encoding="utf-8")
        print(f"[docs_inventory] wrote {out_path.relative_to(repo_root)}")
        return 0

    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

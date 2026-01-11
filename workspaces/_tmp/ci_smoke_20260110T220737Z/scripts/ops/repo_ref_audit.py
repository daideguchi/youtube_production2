#!/usr/bin/env python3
"""
repo_ref_audit.py — repo内パスの参照棚卸し（参照ゼロ検出の基礎ツール）

目的:
  - 「このファイル/ディレクトリはどこから参照されているか？」を機械的に可視化する
  - "参照ゼロ（コード参照ゼロ）" を棚卸しして、SSOT更新→archive-first削除の材料にする

使い方:
  # 例: 特定ファイルの参照を調べる
  python3 scripts/ops/repo_ref_audit.py --target prompts/youtube_description_prompt.txt --stdout

  # 例: globでまとめて（最大200件まで）
  python3 scripts/ops/repo_ref_audit.py --target "packages/**/README.md" --max-targets 200 --stdout

  # 例: JSONで出す（他ツール/人間レビュー用）
  python3 scripts/ops/repo_ref_audit.py --target "scripts/**/*.py" --max-targets 200 --json

  # 例: "コード参照" から scripts/ を除外したい場合（docstring等の自己参照を避ける）
  python3 scripts/ops/repo_ref_audit.py --target prompts/youtube_description_prompt.txt \
    --code-root apps --code-root packages --code-root tests --stdout
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

from _bootstrap import bootstrap


@dataclass(frozen=True)
class RefResult:
    target: str
    exists: bool
    size_bytes: int | None
    created: str
    updated: str
    code_refs: int
    docs_refs: int
    code_example: str | None
    docs_example: str | None


_GIT_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _run_rg(*, repo_root: Path, pattern: str, roots: Sequence[str]) -> list[str]:
    cmd = [
        "rg",
        "-n",
        "--no-heading",
        "--fixed-strings",
        pattern,
        *roots,
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:  # pragma: no cover
        raise RuntimeError("ripgrep (rg) not found") from exc
    if proc.returncode not in (0, 1):
        raise RuntimeError(proc.stderr.strip() or "rg failed")
    return [line for line in proc.stdout.splitlines() if line.strip()]


def _count_refs(
    *,
    repo_root: Path,
    target_rel: str,
    search_roots: Sequence[str],
) -> tuple[int, str | None]:
    hits = _run_rg(repo_root=repo_root, pattern=target_rel, roots=search_roots)
    filtered: list[str] = []
    for line in hits:
        # format: path:line:content
        try:
            file_part, line_no, _ = line.split(":", 2)
        except ValueError:
            continue
        file_part = file_part.lstrip("./")
        if file_part == target_rel:
            continue
        filtered.append(f"{file_part}:{line_no}")
    filtered.sort()
    return len(filtered), (filtered[0] if filtered else None)


def _iter_targets(repo_root: Path, patterns: Iterable[str]) -> list[str]:
    out: list[str] = []
    for raw in patterns:
        pat = (raw or "").strip()
        if not pat:
            continue
        matched = False

        # repo-relative explicit path
        if (repo_root / pat).exists() and (repo_root / pat).is_file():
            rel = (repo_root / pat).relative_to(repo_root).as_posix()
            out.append(rel)
            matched = True
            continue

        # glob fallback (rg/glob semantics are close enough for ops use)
        for match in repo_root.glob(pat):
            if not match.is_file():
                continue
            rel = match.relative_to(repo_root).as_posix()
            out.append(rel)

            matched = True

        # allow auditing a path string even after deletion (no file match)
        if not matched:
            has_glob = any(ch in pat for ch in ("*", "?", "[", "]"))
            if not has_glob and ("/" in pat or "." in Path(pat).name):
                out.append(pat.lstrip("./"))
    return sorted(set(out))


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _git_first_seen_dates(
    *,
    repo_root: Path,
    paths: set[str],
    reverse: bool,
) -> dict[str, str]:
    """
    Returns a map {path: YYYY-MM-DD} for the first time each path appears in `git log`.

    - reverse=False: newest-first => first-seen == latest commit date per path
    - reverse=True:  oldest-first => first-seen == earliest commit date per path
    """
    if not paths:
        return {}

    cmd = [
        "git",
        "log",
        "--date=short",
        "--format=%ad",
        "--name-only",
    ]
    if reverse:
        cmd.insert(2, "--reverse")
    cmd += ["--", *sorted(paths)]

    proc = subprocess.run(
        cmd,
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    if proc.returncode != 0:
        return {}

    found: dict[str, str] = {}
    current_date = ""
    for raw in proc.stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        if _GIT_DATE_RE.match(line):
            current_date = line
            continue
        if not current_date:
            continue
        if line in paths and line not in found:
            found[line] = current_date
            if len(found) >= len(paths):
                break
    return found


def _git_created_updated(repo_root: Path, paths: list[str]) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {p: ("-", "-") for p in paths}
    if not paths:
        return out

    latest_all: dict[str, str] = {}
    earliest_all: dict[str, str] = {}

    for chunk in _chunks(paths, size=120):
        chunk_set = set(chunk)
        latest_all.update(_git_first_seen_dates(repo_root=repo_root, paths=chunk_set, reverse=False))
        earliest_all.update(_git_first_seen_dates(repo_root=repo_root, paths=chunk_set, reverse=True))

    for p in paths:
        out[p] = (earliest_all.get(p, "-"), latest_all.get(p, "-"))
    return out


def _render_markdown(results: list[RefResult]) -> str:
    lines: list[str] = []
    lines.append("# REPO_REF_AUDIT — 参照棚卸し（自動生成）")
    lines.append("")
    lines.append("| target | created | updated | code_refs | docs_refs | code_example | docs_example |")
    lines.append("| --- | ---: | ---: | ---: | ---: | --- | --- |")
    for r in results:
        lines.append(
            f"| `{r.target}` | {r.created} | {r.updated} | {r.code_refs} | {r.docs_refs} | {r.code_example or '—'} | {r.docs_example or '—'} |"
        )
    lines.append("")
    zeros = [r.target for r in results if r.exists and r.code_refs == 0 and r.docs_refs == 0]
    if zeros:
        lines.append("## refs=0（コード参照ゼロ + Docs参照ゼロ）")
        lines.append("")
        for p in zeros:
            lines.append(f"- `{p}`")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit repo-relative path references via ripgrep (fixed-string).")
    ap.add_argument("--target", action="append", default=[], help="repo-relative path or glob (repeatable)")
    ap.add_argument("--max-targets", type=int, default=500, help="safety limit")
    ap.add_argument(
        "--code-root",
        action="append",
        default=[],
        help="search root for code refs (repeatable; default: apps/packages/scripts/tests)",
    )
    ap.add_argument(
        "--docs-root",
        action="append",
        default=[],
        help="search root for docs refs (repeatable; default: ssot/README.md/START_HERE.md/prompts)",
    )
    ap.add_argument("--json", action="store_true", help="emit JSON array to stdout")
    ap.add_argument("--stdout", action="store_true", help="emit markdown to stdout (default)")
    args = ap.parse_args()

    repo_root = bootstrap(load_env=False)
    targets = _iter_targets(repo_root, args.target)
    if not targets:
        print("[repo_ref_audit] no targets matched", file=sys.stderr)
        return 2
    if len(targets) > args.max_targets:
        print(f"[repo_ref_audit] too many targets: {len(targets)} > {args.max_targets}", file=sys.stderr)
        return 2

    code_roots = args.code_root or ["apps", "packages", "scripts", "tests"]
    docs_roots = args.docs_root or ["ssot", "README.md", "START_HERE.md", "prompts"]

    results: list[RefResult] = []
    created_updated = _git_created_updated(repo_root, targets)
    for rel in targets:
        path = repo_root / rel
        exists = path.exists()
        size_bytes = path.stat().st_size if exists else None
        code_refs, code_ex = _count_refs(repo_root=repo_root, target_rel=rel, search_roots=code_roots)
        docs_refs, docs_ex = _count_refs(repo_root=repo_root, target_rel=rel, search_roots=docs_roots)
        created, updated = created_updated.get(rel, ("-", "-"))
        results.append(
            RefResult(
                target=rel,
                exists=exists,
                size_bytes=size_bytes,
                created=created,
                updated=updated,
                code_refs=code_refs,
                docs_refs=docs_refs,
                code_example=code_ex,
                docs_example=docs_ex,
            )
        )

    if args.json:
        print(json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2))
        return 0

    print(_render_markdown(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

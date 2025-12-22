#!/usr/bin/env python3
"""
Generate `ssot/OPS_SCRIPTS_INVENTORY.md`.

This is a safety tool for multi-agent operation:
  - lists every file under `scripts/**`
  - classifies P0/P1/P2/P3 from `ssot/OPS_SCRIPTS_PHASE_CLASSIFICATION.md`
  - shows references (apps/packages/ui/ssot/README/other) excluding the inventory itself

Usage:
  python3 scripts/ops/scripts_inventory.py --write
  python3 scripts/ops/scripts_inventory.py --stdout
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from _bootstrap import bootstrap


@dataclass(frozen=True)
class RefLoc:
    file: str
    line: int


SCRIPT_PATH_RE = re.compile(r"scripts/[A-Za-z0-9_./-]+\.(?:py|sh|md)")
MODULE_RE = re.compile(r"-m\s+scripts\.([A-Za-z0-9_]+)")


def _collect_scripts(repo_root: Path) -> list[str]:
    scripts_root = repo_root / "scripts"
    out: list[str] = []
    for p in scripts_root.rglob("*"):
        if p.is_dir():
            continue
        rel = p.relative_to(repo_root).as_posix()
        if "/__pycache__/" in rel:
            continue
        if rel.startswith("scripts/_adhoc/") and not rel.endswith("README.md"):
            continue
        out.append(rel)
    return sorted(out)


def _parse_ssot_classification(repo_root: Path) -> tuple[set[str], set[str], set[str]]:
    cls_path = repo_root / "ssot" / "OPS_SCRIPTS_PHASE_CLASSIFICATION.md"
    text = cls_path.read_text(encoding="utf-8")
    section: str | None = None
    p0: set[str] = set()
    p1: set[str] = set()
    p2: set[str] = set()

    for line in text.splitlines():
        if line.startswith("## 2."):
            section = "p0"
        elif line.startswith("## 3."):
            section = "p1"
        elif line.startswith("## 4."):
            section = "p2"
        elif line.startswith("## "):
            section = None

        if section not in {"p0", "p1", "p2"}:
            continue

        for m in SCRIPT_PATH_RE.findall(line):
            if section == "p0":
                p0.add(m)
            elif section == "p1":
                p1.add(m)
            else:
                p2.add(m)

        for mod in MODULE_RE.findall(line):
            candidate = f"scripts/{mod}.py"
            if not (repo_root / candidate).exists():
                continue
            if section == "p0":
                p0.add(candidate)
            elif section == "p1":
                p1.add(candidate)
            else:
                p2.add(candidate)

    return p0, p1, p2


def _rg_lines(*, repo_root: Path, pattern: str) -> list[str]:
    cmd = [
        "rg",
        "-n",
        "--no-heading",
        "--glob",
        "!scripts/**",
        "--glob",
        "!ssot/OPS_SCRIPTS_INVENTORY.md",
        "-e",
        pattern,
        ".",
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    if proc.returncode not in (0, 1):
        raise RuntimeError(proc.stderr.strip() or "rg failed")
    return proc.stdout.splitlines()


def _collect_refs(repo_root: Path, scripts: Iterable[str]) -> dict[str, set[RefLoc]]:
    refs: dict[str, set[RefLoc]] = {s: set() for s in scripts}

    # 1) direct path mentions
    for line in _rg_lines(repo_root=repo_root, pattern=r"scripts/[A-Za-z0-9_./-]+\.(?:py|sh|md)"):
        try:
            file_part, line_no, content = line.split(":", 2)
            line_i = int(line_no)
        except Exception:
            continue
        for m in SCRIPT_PATH_RE.findall(content):
            if m in refs:
                refs[m].add(RefLoc(file=file_part.lstrip("./"), line=line_i))

    # 2) python -m scripts.<module>
    for line in _rg_lines(repo_root=repo_root, pattern=r"-m\s+scripts\.[A-Za-z0-9_]+"):
        try:
            file_part, line_no, content = line.split(":", 2)
            line_i = int(line_no)
        except Exception:
            continue
        for mod in re.findall(r"-m\s+scripts\.([A-Za-z0-9_]+)", content):
            candidate = f"scripts/{mod}.py"
            if candidate in refs:
                refs[candidate].add(RefLoc(file=file_part.lstrip("./"), line=line_i))

    # 3) _script_path("...") / _script_path('...')
    for line in _rg_lines(repo_root=repo_root, pattern=r"_script_path\("):
        try:
            file_part, line_no, content = line.split(":", 2)
            line_i = int(line_no)
        except Exception:
            continue
        m = re.search(r"_script_path\(\s*['\"]([^'\"]+)['\"]\s*\)", content)
        if not m:
            continue
        candidate = f"scripts/{m.group(1)}"
        if candidate in refs:
            refs[candidate].add(RefLoc(file=file_part.lstrip("./"), line=line_i))

    return refs


def _phase_for(path: str) -> str:
    if path.startswith("scripts/ops/"):
        return "OPS"
    if path.startswith("scripts/youtube_publisher/"):
        return "PUBLISH"
    base = Path(path).name.lower()

    if base.startswith("agent_") or "agent" in base or "orchestr" in base:
        return "COORD"
    if "planning" in base:
        return "PLANNING"
    if "a_text" in base or "episode" in base or "alignment" in base or base.startswith("sanitize_") or base.startswith("expand_"):
        return "SCRIPT"
    if "tts" in base or "audio" in base or "voicevox" in base or "subtitles" in base or "srt" in base:
        return "AUDIO"
    if "capcut" in base or "remotion" in base or "video" in base or "image" in base or "srt2images" in base:
        return "VIDEO"
    if "thumbnail" in base or "trend" in base:
        return "THUMB"
    if "drive" in base or "youtube" in base or "publish" in base or "oauth" in base:
        return "PUBLISH"
    if "cleanup" in base or "purge" in base or "archive" in base or "restore" in base or "snapshot" in base or "audit" in base:
        return "OPS"
    return "MISC"


def _ref_summary(locs: set[RefLoc]) -> tuple[str, str]:
    counts: dict[str, int] = defaultdict(int)
    example = ""
    preferred = ""

    for loc in sorted(locs, key=lambda x: (x.file, x.line)):
        top = loc.file.split("/", 1)[0]
        if top in {"apps", "packages", "ui", "ssot"}:
            key = top
        elif loc.file == "README.md" or loc.file.startswith("README"):
            key = "README"
        else:
            key = "other"
        counts[key] += 1
        if not example:
            example = f"{loc.file}:{loc.line}"
        if not preferred and key in {"apps", "packages", "ui"}:
            preferred = f"{loc.file}:{loc.line}"

    parts = []
    for k in ["apps", "packages", "ui", "ssot", "README", "other"]:
        if counts.get(k):
            parts.append(f"{k}={counts[k]}")
    if not parts:
        parts = ["refs=0"]
    return " ".join(parts), (preferred or example or "-")


def _render_markdown(
    *,
    scripts: list[str],
    p0: set[str],
    p1: set[str],
    p2: set[str],
    refs: dict[str, set[RefLoc]],
) -> str:
    lines: list[str] = []
    lines += ["# OPS_SCRIPTS_INVENTORY — scripts/ 全ファイル棚卸し（工程別 / 使う・使わない）", ""]
    lines += ["生成:", "- `python3 scripts/ops/scripts_inventory.py --write`", ""]
    lines += [
        "目的:",
        "- `scripts/**` を **全量**列挙し、工程（Phase）と分類（P0/P1/P2/P3）を 1 行ずつ確定する。",
        "- ゴミ判定ミス（例: `run_srt2images.sh` のような間接呼び出し）を防ぐため、ref（参照元）も併記する。",
        "",
    ]
    lines += ["正本:", "- フロー: `ssot/OPS_CONFIRMED_PIPELINE_FLOW.md`", "- 入口/方針: `ssot/OPS_SCRIPTS_PHASE_CLASSIFICATION.md`", ""]
    lines += [
        "凡例:",
        "- `P0`: 正規入口（主線・まず叩く）",
        "- `P1`: 付帯/診断（使うことはあるが主線ではない）",
        "- `P2`: 禁止（絶対に使わない / 削除候補）",
        "- `P3`: 一時（`scripts/_adhoc/`。原則git管理しない）",
        "",
    ]
    lines += [
        "ref の見方:",
        "- `apps=*` / `packages=*` / `ui=*` は **コード参照**（自動実行の可能性が高い）",
        "- `ssot=*` / `README=*` は **ドキュメント参照**（手動実行の可能性）",
        "- `refs=0` かつ SSOT未記載のものは “未確認レガシー候補” として扱い、削除は `PLAN_LEGACY_AND_TRASH_CLASSIFICATION` の条件を満たしてから行う。",
        "",
    ]
    lines += [
        "---",
        "",
        "| script | phase | P | listed-in-SSOT | refs (apps/packages/ui/ssot/readme/other) | example ref |",
        "|---|---:|:--:|:--:|---:|---|",
    ]

    for s in scripts:
        phase = _phase_for(s)
        if s.startswith("scripts/_adhoc/"):
            p = "P3"
            listed = "yes"
        elif s in p0:
            p = "P0"
            listed = "yes"
        elif s in p2:
            p = "P2"
            listed = "yes"
        elif s in p1:
            p = "P1"
            listed = "yes"
        else:
            p = "P1"
            listed = "no"

        refs_str, example = _ref_summary(refs.get(s, set()))
        lines.append(f"| `{s}` | {phase} | {p} | {listed} | {refs_str} | `{example}` |")

    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate ssot/OPS_SCRIPTS_INVENTORY.md (scripts inventory).")
    ap.add_argument("--write", action="store_true", help="Write to ssot/OPS_SCRIPTS_INVENTORY.md")
    ap.add_argument("--stdout", action="store_true", help="Print markdown to stdout")
    args = ap.parse_args()

    repo_root = bootstrap()

    scripts = _collect_scripts(repo_root)
    p0, p1, p2 = _parse_ssot_classification(repo_root)
    refs = _collect_refs(repo_root, scripts)
    md = _render_markdown(scripts=scripts, p0=p0, p1=p1, p2=p2, refs=refs)

    out_path = repo_root / "ssot" / "OPS_SCRIPTS_INVENTORY.md"
    if args.write:
        out_path.write_text(md, encoding="utf-8")
    if args.stdout or not args.write:
        sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

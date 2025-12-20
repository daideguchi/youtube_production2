#!/usr/bin/env python3
"""
Deterministic lint for A-text (assembled script).

This complements LLM Judge/Fix by catching obvious mechanical issues:
- SSOT global-rule violations (URLs, bullets, separators, length, etc.)
- Excessive "まとめ重複" / repetitive closing phrases (e.g. 「最後に」連打)
- Near-duplicate adjacent paragraphs (simple similarity heuristic)

Outputs a report under logs/regression/a_text_lint/.

Usage:
  python scripts/ops/a_text_lint.py --channel CH07 --video 009
  python scripts/ops/a_text_lint.py --path workspaces/scripts/CH07/009/content/assembled.md
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _bootstrap import bootstrap

PROJECT_ROOT = bootstrap(load_env=False)

from factory_common.paths import logs_root, repo_root, script_data_root
from packages.script_pipeline.sot import load_status
from packages.script_pipeline.validator import validate_a_text


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _normalize_channel(ch: str) -> str:
    s = (ch or "").strip().upper()
    if re.fullmatch(r"CH\\d{2}", s):
        return s
    m = re.fullmatch(r"CH(\\d+)", s)
    if m:
        return f"CH{int(m.group(1)):02d}"
    return s


def _normalize_video(no: str) -> str:
    s = (no or "").strip()
    try:
        return f"{int(s):03d}"
    except Exception:
        return s.zfill(3)


def _canonical_a_text_path(base: Path) -> Path:
    content_dir = base / "content"
    human = content_dir / "assembled_human.md"
    assembled = content_dir / "assembled.md"
    return human if human.exists() else assembled


def _paragraphs(text: str) -> list[str]:
    blocks = [b.strip() for b in re.split(r"\\n\\s*\\n", (text or "").replace("\\r\\n", "\\n").replace("\\r", "\\n"))]
    return [b for b in blocks if b]


def _char_ngrams(text: str, n: int = 3) -> set[str]:
    t = re.sub(r"\\s+", "", text or "")
    if len(t) < n:
        return set()
    return {t[i : i + n] for i in range(len(t) - n + 1)}


@dataclass(frozen=True)
class LintIssue:
    severity: str  # error|warning
    code: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {"severity": self.severity, "code": self.code, "message": self.message}


def _repetition_issues(text: str) -> list[LintIssue]:
    issues: list[LintIssue] = []
    normalized = (text or "")

    phrase_limits: list[tuple[str, int, int]] = [
        ("最後に", 1, 2),  # warn >1, error >2
        ("もう一度", 1, 2),
        ("お願いがあります", 1, 1),
        ("合掌", 1, 2),
    ]
    for phrase, warn_gt, err_gt in phrase_limits:
        cnt = normalized.count(phrase)
        if cnt > err_gt:
            issues.append(
                LintIssue(
                    severity="error",
                    code="repetition_phrase_excess",
                    message=f"Phrase '{phrase}' appears {cnt} times (>{err_gt}). Consider merging the closing/summary.",
                )
            )
        elif cnt > warn_gt:
            issues.append(
                LintIssue(
                    severity="warning",
                    code="repetition_phrase_warning",
                    message=f"Phrase '{phrase}' appears {cnt} times (>{warn_gt}). Risk of 'まとめ重複'.",
                )
            )

    # exact repeated lines (excluding blanks and pause markers)
    lines = [ln.strip() for ln in normalized.replace("\\r\\n", "\\n").replace("\\r", "\\n").split("\\n")]
    lines = [ln for ln in lines if ln and ln != "---"]
    counts = Counter(lines)
    repeated = [(c, ln) for ln, c in counts.items() if c >= 2]
    repeated.sort(reverse=True)
    for c, ln in repeated[:8]:
        issues.append(
            LintIssue(
                severity="warning",
                code="repetition_exact_line",
                message=f"Exact line repeated {c}x: {ln[:80]}",
            )
        )

    # adjacent paragraph similarity (jaccard of char 3-grams)
    paras = _paragraphs(normalized)
    similar = 0
    for i in range(len(paras) - 1):
        a = _char_ngrams(paras[i])
        b = _char_ngrams(paras[i + 1])
        if not a or not b:
            continue
        j = len(a & b) / max(1, len(a | b))
        if j >= 0.28:
            similar += 1
    if similar:
        issues.append(
            LintIssue(
                severity="warning",
                code="repetition_similar_paragraphs",
                message=f"Found {similar} adjacent paragraph pairs with high similarity (>=0.28). Consider consolidating.",
            )
        )
    return issues


def lint_a_text(channel: str | None, video: str | None, path: Path | None) -> dict[str, Any]:
    if path is None:
        if not channel or not video:
            raise ValueError("Either --path or (--channel and --video) is required")
        ch = _normalize_channel(channel)
        no = _normalize_video(video)
        base = script_data_root() / ch / no
        script_path = _canonical_a_text_path(base)
        st = load_status(ch, no)
        meta = st.metadata or {}
        resolved = script_path
    else:
        resolved = path
        if not resolved.is_absolute():
            resolved = repo_root() / resolved
        meta = {}
        ch = _normalize_channel(channel or "")
        no = _normalize_video(video or "")

    try:
        text = resolved.read_text(encoding="utf-8")
    except Exception as exc:
        return {
            "schema": "ytm.a_text_lint.v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "ok": False,
            "path": str(resolved),
            "issues": [
                LintIssue("error", "read_failed", f"Cannot read {resolved}: {exc}").as_dict(),
            ],
        }

    issues: list[dict[str, Any]] = []
    hard_issues, stats = validate_a_text(text, meta)
    for it in hard_issues:
        sev = str(it.get("severity") or "error").lower()
        issues.append(
            {
                "severity": sev,
                "code": it.get("code"),
                "message": it.get("message"),
                "line": it.get("line"),
            }
        )
    issues.extend([i.as_dict() for i in _repetition_issues(text)])

    ok = not any(str(i.get("severity")) == "error" for i in issues)
    by_sev = Counter(str(i.get("severity")) for i in issues)
    by_code = Counter(str(i.get("code")) for i in issues)

    return {
        "schema": "ytm.a_text_lint.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "channel": ch or None,
        "video": no or None,
        "path": str(resolved),
        "ok": ok,
        "stats": stats,
        "counts": {"by_severity": dict(by_sev), "by_code": dict(by_code)},
        "issues": issues,
    }


def _write_report(report: dict[str, Any], label: str, *, write_latest: bool) -> tuple[Path, Path]:
    out_dir = logs_root() / "regression" / "a_text_lint"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = _utc_now_compact()
    json_path = out_dir / f"a_text_lint_{label}__{ts}.json"
    md_path = out_dir / f"a_text_lint_{label}__{ts}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    counts = report.get("counts") if isinstance(report, dict) else {}
    issues = report.get("issues") if isinstance(report, dict) else []
    by_sev = counts.get("by_severity") if isinstance(counts, dict) else {}
    by_code = counts.get("by_code") if isinstance(counts, dict) else {}

    lines: list[str] = []
    lines.append(f"# a_text_lint report: {label}")
    lines.append("")
    lines.append(f"- generated_at: {report.get('generated_at')}")
    lines.append(f"- path: {report.get('path')}")
    lines.append(f"- ok: {report.get('ok')}")
    lines.append(f"- counts.by_severity: {json.dumps(by_sev, ensure_ascii=False)}")
    lines.append(f"- counts.by_code: {json.dumps(by_code, ensure_ascii=False)}")
    lines.append("")
    lines.append("## Issues (first 80)")
    if isinstance(issues, list):
        for it in issues[:80]:
            if not isinstance(it, dict):
                continue
            sev = it.get("severity")
            code = it.get("code")
            line = it.get("line")
            msg = it.get("message")
            where = f":{line}" if isinstance(line, int) else ""
            lines.append(f"- [{sev}] {code}{where}: {msg}")
    md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    if write_latest:
        latest_json = out_dir / f"a_text_lint_{label}__latest.json"
        latest_md = out_dir / f"a_text_lint_{label}__latest.md"
        latest_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        latest_md.write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")

    return json_path, md_path


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--path", help="A-text path (repo-relative or absolute)")
    g.add_argument("--channel", help="Channel code like CH07 (requires --video)")
    ap.add_argument("--video", help="Video number like 009 (required with --channel)")
    ap.add_argument("--write-latest", action="store_true", help="Also write *_latest.json/md (overwrite)")
    args = ap.parse_args()

    path = Path(args.path) if args.path else None
    channel = args.channel
    video = args.video
    if channel and not video:
        ap.error("--channel requires --video")  # pragma: no cover

    label = "PATH" if path else f"{_normalize_channel(channel or '')}_{_normalize_video(video or '')}"
    report = lint_a_text(channel, video, path)
    json_path, md_path = _write_report(report, label, write_latest=bool(args.write_latest))
    print(f"Wrote: {json_path}")
    print(f"Wrote: {md_path}")
    return 0 if report.get("ok", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())

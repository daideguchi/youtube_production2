#!/usr/bin/env python3
"""
Lint planning CSVs (Planning SoT) for coherence and contamination.

Why:
- Planning rows often get "contaminated" (e.g. a summary from another episode),
  which misleads humans/agents and can cause title/content misalignment.
- This tool is deterministic (no LLM), safe to run anytime, and writes a report
  under logs/regression/ for auditability.

Usage:
  python scripts/ops/planning_lint.py --channel CH07
  python scripts/ops/planning_lint.py --all
  python scripts/ops/planning_lint.py --csv workspaces/planning/channels/CH07.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from _bootstrap import bootstrap

PROJECT_ROOT = bootstrap(load_env=False)

from factory_common.paths import logs_root, repo_root


_TAG_RE = re.compile(r"^\s*【([^】]{1,30})】")


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _extract_tag(text: str) -> str | None:
    s = (text or "").strip()
    m = _TAG_RE.match(s)
    if not m:
        return None
    inner = (m.group(1) or "").strip()
    return inner or None


def _normalize_channel(ch: str) -> str:
    s = (ch or "").strip().upper()
    if re.fullmatch(r"CH\d{2}", s):
        return s
    m = re.fullmatch(r"CH(\d+)", s)
    if m:
        return f"CH{int(m.group(1)):02d}"
    return s


def _read_channels_config() -> dict[str, Any]:
    path = repo_root() / "packages" / "script_pipeline" / "channels" / "channels.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _planning_csv_path(channel: str) -> Path:
    return repo_root() / "workspaces" / "planning" / "channels" / f"{channel}.csv"


def _video_number_from_row(row: dict[str, str]) -> str:
    for key in ("動画番号", "No.", "VideoNumber", "video_number", "video"):
        if key in row and (row.get(key) or "").strip():
            raw = (row.get(key) or "").strip()
            # Prefer numeric normalization when possible.
            try:
                return f"{int(raw):03d}"
            except Exception:
                return raw
    # fallback: look for CHxx-NNN in known id fields
    for key in ("動画ID", "台本番号", "ScriptID", "script_id"):
        v = (row.get(key) or "").strip()
        m = re.search(r"\bCH\d{2}-(\d{3})\b", v)
        if m:
            return m.group(1)
    return "???"


@dataclass(frozen=True)
class LintIssue:
    channel: str
    video: str
    row_index: int
    severity: str  # "error" | "warning"
    code: str
    message: str
    columns: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "video": self.video,
            "row_index": self.row_index,
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "columns": self.columns,
        }


def _required_columns_for_channel(channel: str) -> list[str]:
    cfg = _read_channels_config()
    key = channel.lower()
    base = ["タイトル"]
    ch_cfg = cfg.get(key) if isinstance(cfg, dict) else None
    specific = []
    if isinstance(ch_cfg, dict):
        sc = ch_cfg.get("specific_columns")
        if isinstance(sc, list):
            specific = [str(x) for x in sc if str(x).strip()]
    # Always treat these identifiers as required for lint context, but allow header variants.
    return base + specific


def _detect_contamination_signals(row: dict[str, str]) -> list[tuple[str, str]]:
    # (code, matched_column)
    signals: list[tuple[str, str]] = []
    patterns: list[tuple[str, re.Pattern[str]]] = [
        ("contains_deepnight_radio_opener", re.compile(r"深夜の偉人ラジオへようこそ")),
        ("contains_bullet_like_opener", re.compile(r"^\s*[-*•]|^\s*・\s+", flags=re.MULTILINE)),
    ]
    for col, val in row.items():
        s = str(val or "")
        if not s.strip():
            continue
        for code, pat in patterns:
            if pat.search(s):
                signals.append((code, col))
    return signals


def lint_planning_csv(csv_path: Path, channel: str, *, tag_mismatch_is_error: bool = False) -> dict[str, Any]:
    issues: list[LintIssue] = []
    channel = _normalize_channel(channel)
    required = _required_columns_for_channel(channel)

    if not csv_path.exists():
        return {
            "schema": "ytm.planning_lint.v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "channel": channel,
            "csv_path": str(csv_path),
            "ok": False,
            "issues": [
                LintIssue(
                    channel=channel,
                    video="???",
                    row_index=0,
                    severity="error",
                    code="missing_csv",
                    message=f"CSV not found: {csv_path}",
                    columns=[],
                ).as_dict()
            ],
        }

    with csv_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        rows = list(reader)

    # Header-level checks
    missing_required = [c for c in required if c not in headers]
    # Allow common variants for these:
    id_ok = any(h in headers for h in ("動画番号", "No.", "VideoNumber", "video_number", "video"))
    if not id_ok:
        missing_required.append("動画番号 (or No.)")

    if missing_required:
        issues.append(
            LintIssue(
                channel=channel,
                video="???",
                row_index=0,
                severity="error",
                code="missing_required_columns",
                message="Missing required columns: " + ", ".join(missing_required),
                columns=missing_required,
            )
        )

    # Row-level checks
    for idx, row in enumerate(rows, start=1):
        video = _video_number_from_row(row)
        title = (row.get("タイトル") or "").strip()
        if not title:
            issues.append(
                LintIssue(
                    channel=channel,
                    video=video,
                    row_index=idx,
                    severity="error",
                    code="missing_title",
                    message="タイトル is empty",
                    columns=["タイトル"],
                )
            )
            continue

        title_tag = _extract_tag(title)
        summary = (row.get("内容（企画要約）") or "").strip()
        summary_tag = _extract_tag(summary) if summary else None
        if title_tag and summary_tag and title_tag != summary_tag:
            issues.append(
                LintIssue(
                    channel=channel,
                    video=video,
                    row_index=idx,
                    severity="error" if tag_mismatch_is_error else "warning",
                    code="tag_mismatch_title_vs_content_summary",
                    message=f"title tag 【{title_tag}】 != content_summary tag 【{summary_tag}】 (treat content_summary as L2; drop/regenerate)",
                    columns=["タイトル", "内容（企画要約）"],
                )
            )

        # L1 contract-ish checks for commonly required columns.
        for col in ("企画意図", "ターゲット層", "具体的な内容（話の構成案）"):
            if col in required and not (row.get(col) or "").strip():
                issues.append(
                    LintIssue(
                        channel=channel,
                        video=video,
                        row_index=idx,
                        severity="error",
                        code="missing_required_field",
                        message=f"{col} is empty (L1 required for this channel)",
                        columns=[col],
                    )
                )

        # Soft signals for human/agent confusion (not used as pipeline inputs).
        for code, col in _detect_contamination_signals(row):
            issues.append(
                LintIssue(
                    channel=channel,
                    video=video,
                    row_index=idx,
                    severity="warning",
                    code=code,
                    message=f"Possible contamination signal in column: {col}",
                    columns=[col],
                )
            )

    ok = not any(i.severity == "error" for i in issues)
    by_code = Counter(i.code for i in issues)
    by_severity = Counter(i.severity for i in issues)

    return {
        "schema": "ytm.planning_lint.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "channel": channel,
        "csv_path": str(csv_path),
        "rows": len(rows),
        "ok": ok,
        "counts": {"by_severity": dict(by_severity), "by_code": dict(by_code)},
        "issues": [i.as_dict() for i in issues],
    }


def _write_report(report: dict[str, Any], out_dir: Path, label: str) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = _utc_now_compact()
    json_path = out_dir / f"planning_lint_{label}__{ts}.json"
    md_path = out_dir / f"planning_lint_{label}__{ts}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    counts = report.get("counts") if isinstance(report, dict) else {}
    by_sev = counts.get("by_severity") if isinstance(counts, dict) else {}
    by_code = counts.get("by_code") if isinstance(counts, dict) else {}
    issues = report.get("issues") if isinstance(report, dict) else []

    lines: list[str] = []
    lines.append(f"# planning_lint report: {label}")
    lines.append("")
    lines.append(f"- generated_at: {report.get('generated_at')}")
    lines.append(f"- csv_path: {report.get('csv_path')}")
    lines.append(f"- rows: {report.get('rows')}")
    lines.append(f"- ok: {report.get('ok')}")
    lines.append("")
    lines.append("## Counts")
    lines.append(f"- by_severity: {json.dumps(by_sev, ensure_ascii=False)}")
    lines.append(f"- by_code: {json.dumps(by_code, ensure_ascii=False)}")
    lines.append("")
    lines.append("## Issues (first 80)")
    if isinstance(issues, list):
        for it in issues[:80]:
            if not isinstance(it, dict):
                continue
            ch = it.get("channel")
            v = it.get("video")
            sev = it.get("severity")
            code = it.get("code")
            msg = it.get("message")
            cols = it.get("columns")
            lines.append(f"- [{sev}] {ch}/{v} {code}: {msg} (cols={cols})")
    md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return json_path, md_path


def _iter_all_planning_csvs() -> Iterable[tuple[str, Path]]:
    base = repo_root() / "workspaces" / "planning" / "channels"
    if not base.exists():
        return []
    out: list[tuple[str, Path]] = []
    for p in sorted(base.glob("CH*.csv")):
        ch = _normalize_channel(p.stem)
        out.append((ch, p))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--channel", help="Channel code like CH07 (reads workspaces/planning/channels/CH07.csv)")
    g.add_argument("--csv", help="Explicit CSV path (repo-relative or absolute)")
    g.add_argument("--all", action="store_true", help="Lint all workspaces/planning/channels/CH*.csv")
    ap.add_argument(
        "--tag-mismatch-is-error",
        action="store_true",
        help="Treat tag_mismatch_title_vs_content_summary as error (exit non-zero)",
    )
    ap.add_argument("--write-latest", action="store_true", help="Also write *_latest.json/md (overwrite)")
    args = ap.parse_args()

    out_dir = logs_root() / "regression" / "planning_lint"
    reports: list[dict[str, Any]] = []

    targets: list[tuple[str, Path]] = []
    if args.all:
        targets = list(_iter_all_planning_csvs())
    elif args.csv:
        p = Path(args.csv)
        if not p.is_absolute():
            p = repo_root() / p
        # Guess channel from filename if possible.
        guessed = _normalize_channel(p.stem)
        targets = [(guessed or "UNKNOWN", p)]
    else:
        ch = _normalize_channel(str(args.channel))
        targets = [(ch, _planning_csv_path(ch))]

    any_errors = False
    for ch, path in targets:
        rep = lint_planning_csv(path, ch, tag_mismatch_is_error=bool(args.tag_mismatch_is_error))
        reports.append(rep)
        label = ch if len(targets) == 1 else f"{ch}"
        json_path, md_path = _write_report(rep, out_dir, label)
        print(f"Wrote: {json_path}")
        print(f"Wrote: {md_path}")
        if args.write_latest:
            latest_json = out_dir / f"planning_lint_{label}__latest.json"
            latest_md = out_dir / f"planning_lint_{label}__latest.md"
            latest_json.write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            latest_md.write_text(md_path.read_text(encoding='utf-8'), encoding="utf-8")
            print(f"Wrote: {latest_json}")
            print(f"Wrote: {latest_md}")
        if not rep.get("ok", False):
            any_errors = True

    if args.all:
        merged = {
            "schema": "ytm.planning_lint_bundle.v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "reports": reports,
        }
        bundle_json = out_dir / f"planning_lint_ALL__{_utc_now_compact()}.json"
        bundle_json.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote: {bundle_json}")

    return 1 if any_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

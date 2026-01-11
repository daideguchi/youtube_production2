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
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from _bootstrap import bootstrap

PROJECT_ROOT = bootstrap(load_env=False)

from factory_common.paths import logs_root, repo_root
from script_pipeline.tools import planning_requirements


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


def _normalize_tag_for_compare(tag: str | None) -> str:
    s = unicodedata.normalize("NFKC", str(tag or "")).strip()
    # Remove whitespace and common separators/punctuation that often fluctuate in planning tags.
    s = re.sub(r"[\s\u3000・･·、,\.／/\\\-‐‑‒–—―ー〜~]", "", s)
    return s


def _resolve_episode_key(row: dict[str, str]) -> tuple[str, str]:
    """
    Return (raw_key, normalized_key) for episode-duplication detection.

    Primary SoT: キーコンセプト
    Fallbacks (deterministic, channel-agnostic):
    - タイトル先頭の【...】
    - 悩みタグ_メイン / 悩みタグ_サブ
    """
    raw = str(row.get("キーコンセプト") or "").strip()
    if raw:
        return raw, _normalize_tag_for_compare(raw)
    title_tag = _extract_tag(str(row.get("タイトル") or ""))
    if title_tag:
        return title_tag, _normalize_tag_for_compare(title_tag)
    main_tag = str(row.get("悩みタグ_メイン") or "").strip()
    if main_tag:
        return main_tag, _normalize_tag_for_compare(main_tag)
    sub_tag = str(row.get("悩みタグ_サブ") or "").strip()
    if sub_tag:
        return sub_tag, _normalize_tag_for_compare(sub_tag)
    return "", ""


def _normalize_channel(ch: str) -> str:
    s = (ch or "").strip().upper()
    if re.fullmatch(r"CH\d{2}", s):
        return s
    m = re.fullmatch(r"CH(\d+)", s)
    if m:
        return f"CH{int(m.group(1)):02d}"
    return s


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

def _is_published_row(row: dict[str, str]) -> bool:
    progress = str(row.get("進捗") or "").strip()
    if not progress:
        return False
    # Planning SoT treats these as published locks; required planning fields no longer matter.
    if "投稿済み" in progress:
        return True
    if "公開済み" in progress:
        return True
    if progress.lower() in {"published"}:
        return True
    return False

def _maybe_int(value: str) -> Optional[int]:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except Exception:
        return None


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

def _detect_contamination_signals(row: dict[str, str]) -> list[tuple[str, str]]:
    # (code, matched_column)
    signals: list[tuple[str, str]] = []

    def _should_scan_column(code: str, column: str) -> bool:
        col = str(column or "").strip()
        if not col:
            return False
        # Bullet lists are normal in human-facing design instruction columns.
        if code == "contains_bullet_like_opener" and "デザイン指示" in col:
            return False
        # YouTube IDs may start with "-" (valid ID), which previously caused false positives.
        if code == "contains_bullet_like_opener" and col.strip().lower() in {"youtubeid", "youtube_id"}:
            return False
        return True

    patterns: list[tuple[str, re.Pattern[str]]] = [
        ("contains_deepnight_radio_opener", re.compile(r"深夜の偉人ラジオへようこそ")),
        ("contains_bullet_like_opener", re.compile(r"^\s*[-*•]\s+|^\s*・\s+", flags=re.MULTILINE)),
    ]
    for col, val in row.items():
        s = str(val or "")
        if not s.strip():
            continue
        for code, pat in patterns:
            if not _should_scan_column(code, col):
                continue
            if pat.search(s):
                signals.append((code, col))
    return signals


def lint_planning_csv(csv_path: Path, channel: str, *, tag_mismatch_is_error: bool = False) -> dict[str, Any]:
    issues: list[LintIssue] = []
    channel = _normalize_channel(channel)

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

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        rows = list(reader)

    # Episode duplication guard (published inventory).
    # SoT: Planning CSV (進捗=投稿済み/公開済み) + キーコンセプト
    published_key_concepts: dict[str, list[str]] = {}
    published_missing_key_concept: list[str] = []
    unpublished_missing_key_concept: list[str] = []
    if "キーコンセプト" in headers:
        for row in rows:
            if not _is_published_row(row):
                continue
            if not str(row.get("キーコンセプト") or "").strip():
                published_missing_key_concept.append(_video_number_from_row(row))
            key_raw, key_norm = _resolve_episode_key(row)
            if not key_norm:
                continue
            published_key_concepts.setdefault(key_norm, []).append(_video_number_from_row(row))

    # Header-level checks
    missing_required = ["タイトル"] if "タイトル" not in headers else []
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

    # Policy-level required columns (SoT = planning_requirements; aligns with UI planning guard).
    required_cols_union: set[str] = set()
    for row in rows:
        if _is_published_row(row):
            continue
        video = _video_number_from_row(row)
        numeric_video = _maybe_int(video)
        for col in planning_requirements.resolve_required_columns(channel, numeric_video):
            if col:
                required_cols_union.add(col)
    missing_policy_required = [c for c in sorted(required_cols_union) if c not in headers]
    if missing_policy_required:
        issues.append(
            LintIssue(
                channel=channel,
                video="???",
                row_index=0,
                severity="error",
                code="missing_required_columns_by_policy",
                message="Missing required columns (planning_requirements policy): " + ", ".join(missing_policy_required),
                columns=missing_policy_required,
            )
        )

    # Row-level checks
    for idx, row in enumerate(rows, start=1):
        video = _video_number_from_row(row)
        numeric_video = _maybe_int(video)
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
        title_tag_norm = _normalize_tag_for_compare(title_tag)
        summary_tag_norm = _normalize_tag_for_compare(summary_tag)
        if title_tag and summary_tag and title_tag_norm != summary_tag_norm:
            issues.append(
                LintIssue(
                    channel=channel,
                    video=video,
                    row_index=idx,
                    severity="error" if tag_mismatch_is_error else "warning",
                    code="tag_mismatch_title_vs_content_summary",
                    message=(
                        f"内容汚染の可能性: タイトル先頭【{title_tag}】 != 内容（企画要約）先頭【{summary_tag}】。"
                        "タイトルを正として、台本生成では内容（企画要約）などのテーマヒントを無視します（CSV修正推奨）。"
                    ),
                    columns=["タイトル", "内容（企画要約）"],
                )
            )

        # Required fields (deterministic). SoT = planning_requirements (same as UI planning guard).
        if not _is_published_row(row):
            for col in planning_requirements.resolve_required_columns(channel, numeric_video):
                if col not in headers:
                    continue
                if not (row.get(col) or "").strip():
                    issues.append(
                        LintIssue(
                            channel=channel,
                            video=video,
                            row_index=idx,
                            severity="error",
                            code="missing_required_field",
                            message=f"{col} is empty (planning_requirements required)",
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

        # Episode duplication guard: warn when an unpublished row reuses a published key concept.
        if published_key_concepts and not _is_published_row(row):
            if not str(row.get("キーコンセプト") or "").strip():
                unpublished_missing_key_concept.append(video)
            key_raw, key_norm = _resolve_episode_key(row)
            if key_norm:
                published = published_key_concepts.get(key_norm) or []
                if published:
                    published_list = ", ".join(published[:8]) + (" …" if len(published) > 8 else "")
                    issues.append(
                        LintIssue(
                            channel=channel,
                            video=video,
                            row_index=idx,
                            severity="warning",
                            code="duplicate_key_concept_with_published",
                            message=(
                                "採用済み（進捗=投稿済み/公開済み）の回とエピソードキーが重複しています: "
                                f"{key_raw!r} (published={published_list})。"
                                "意図的に被せる場合は、企画意図に差分を明記してください。"
                            ),
                            columns=["キーコンセプト", "タイトル", "進捗"],
                        )
                    )

    # Aggregated warnings: key concept is the primary ops parameter, but legacy rows may not have it populated yet.
    if published_missing_key_concept:
        sample = ", ".join(published_missing_key_concept[:12]) + (" …" if len(published_missing_key_concept) > 12 else "")
        issues.append(
            LintIssue(
                channel=channel,
                video="???",
                row_index=0,
                severity="warning",
                code="missing_key_concept_published_rows",
                message=(
                    f"採用済み（進捗=投稿済み/公開済み）の行で `キーコンセプト` が空です: "
                    f"{len(published_missing_key_concept)}件（例: {sample}）。"
                    "重複検知はタイトル先頭【...】/悩みタグをフォールバックにしますが、厳密運用するなら埋めてください。"
                ),
                columns=["キーコンセプト", "タイトル", "進捗"],
            )
        )
    if unpublished_missing_key_concept:
        sample = ", ".join(unpublished_missing_key_concept[:12]) + (" …" if len(unpublished_missing_key_concept) > 12 else "")
        issues.append(
            LintIssue(
                channel=channel,
                video="???",
                row_index=0,
                severity="warning",
                code="missing_key_concept_unpublished_rows",
                message=(
                    f"未採用（進捗!=投稿済み/公開済み）の行で `キーコンセプト` が空です: "
                    f"{len(unpublished_missing_key_concept)}件（例: {sample}）。"
                    "タイトル先頭【...】/悩みタグで推定できる場合は重複検知できますが、キーコンセプトを埋めると運用が安定します。"
                ),
                columns=["キーコンセプト", "タイトル", "進捗"],
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

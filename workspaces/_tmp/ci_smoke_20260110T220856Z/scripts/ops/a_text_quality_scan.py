#!/usr/bin/env python3
"""
a_text_quality_scan.py — batch quality scan for unposted A-text (NO LLM).

Goal:
- Detect *obvious breakage* deterministically (A-text contract errors).
- Provide a lightweight "content quality" signal via repetition heuristics (ranking only).
- Write one aggregated report (avoid per-episode log spam).

SSOT:
- A-text rules: ssot/ops/OPS_A_TEXT_GLOBAL_RULES.md
- Audit ops: ssot/ops/OPS_DIALOG_AI_SCRIPT_AUDIT.md

Usage:
  python3 scripts/ops/a_text_quality_scan.py --all --write-latest
  python3 scripts/ops/a_text_quality_scan.py --channels CH13,CH14 --write-latest
  python3 scripts/ops/a_text_quality_scan.py --all --write-decisions

Notes:
- This tool does NOT call any LLM APIs.
- This tool does NOT modify scripts or status.json by default.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from _bootstrap import bootstrap

bootstrap(load_env=False)

from factory_common.paths import logs_root, planning_channels_dir, repo_root, script_data_root
from script_pipeline.validator import validate_a_text


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
        digits = "".join(ch for ch in s if ch.isdigit())
        return digits.zfill(3) if digits else s.zfill(3)


SCRIPT_ID_RE = re.compile(r"\\bCH\\d{2}-\\d{3}\\b")


@dataclass(frozen=True)
class PlanningRow:
    script_id: str
    posted: bool


def _parse_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(str(value).strip())
    except Exception:
        return None


def _extract_script_id_from_row(joined: str) -> Optional[str]:
    m = SCRIPT_ID_RE.search(joined or "")
    return m.group(0) if m else None


def load_planning_index() -> dict[str, PlanningRow]:
    """
    Best-effort Planning CSV index across all channels.
    Keyed by script_id (e.g., CH15-021).
    """
    out: dict[str, PlanningRow] = {}
    for csv_path in sorted(planning_channels_dir().glob("CH*.csv")):
        if csv_path.name.lower().endswith("_planning_template.csv"):
            continue
        try:
            text = csv_path.read_text(encoding="utf-8-sig")
        except Exception:
            continue

        rows = list(csv.reader(text.splitlines()))
        if not rows:
            continue
        header = rows[0]
        idx: dict[str, int] = {str(name).strip(): i for i, name in enumerate(header)}

        def col(row: list[str], name: str) -> str:
            i = idx.get(name)
            if i is None or i < 0 or i >= len(row):
                return ""
            return str(row[i] or "")

        for row in rows[1:]:
            joined = " ".join([str(c).strip() for c in row if str(c).strip()])
            if not joined:
                continue

            script_id = _extract_script_id_from_row(joined)
            if not script_id:
                ch = _normalize_channel(col(row, "チャンネル") or csv_path.stem)
                no_raw = col(row, "動画番号") or col(row, "No.") or col(row, "VideoNumber")
                no = _parse_int(no_raw)
                if ch and no is not None:
                    script_id = f"{ch}-{int(no):03d}"
            if not script_id:
                continue

            posted = ("投稿済み" in joined) or ("公開済み" in joined)
            out[script_id] = PlanningRow(script_id=script_id, posted=posted)
    return out


def _canonical_a_text_path(base: Path) -> Path:
    content_dir = base / "content"
    human = content_dir / "assembled_human.md"
    assembled = content_dir / "assembled.md"
    return human if human.exists() else assembled


def _read_text_best_effort(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _paragraphs(text: str) -> list[str]:
    blocks = [b.strip() for b in re.split(r"\\n\\s*\\n", (text or "").replace("\\r\\n", "\\n").replace("\\r", "\\n"))]
    return [b for b in blocks if b]


def _char_ngrams(text: str, n: int = 3) -> set[str]:
    t = re.sub(r"\\s+", "", text or "")
    if len(t) < n:
        return set()
    return {t[i : i + n] for i in range(len(t) - n + 1)}


def repetition_metrics(text: str) -> dict[str, Any]:
    """
    Heuristic content-quality signals.
    This is NOT a hard gate; used for ranking and review.
    """
    normalized = (text or "")
    issues: list[dict[str, Any]] = []

    phrase_limits: list[tuple[str, int, int]] = [
        ("最後に", 1, 2),
        ("もう一度", 1, 2),
        ("お願いがあります", 1, 1),
        ("合掌", 1, 2),
    ]
    for phrase, warn_gt, err_gt in phrase_limits:
        cnt = normalized.count(phrase)
        if cnt > err_gt:
            issues.append(
                {
                    "severity": "error",
                    "code": "repetition_phrase_excess",
                    "message": f"'{phrase}' appears {cnt}x (>{err_gt})",
                }
            )
        elif cnt > warn_gt:
            issues.append(
                {
                    "severity": "warning",
                    "code": "repetition_phrase_warning",
                    "message": f"'{phrase}' appears {cnt}x (>{warn_gt})",
                }
            )

    lines = [ln.strip() for ln in normalized.replace("\\r\\n", "\\n").replace("\\r", "\\n").split("\\n")]
    lines = [ln for ln in lines if ln and ln != "---"]
    counts = Counter(lines)
    repeated = [(c, ln) for ln, c in counts.items() if c >= 2]
    repeated.sort(reverse=True)

    if repeated:
        for c, ln in repeated[:8]:
            issues.append(
                {
                    "severity": "warning",
                    "code": "repetition_exact_line",
                    "message": f"line repeated {c}x: {ln[:80]}",
                }
            )

    paras = _paragraphs(normalized)
    similar_pairs = 0
    for i in range(len(paras) - 1):
        a = _char_ngrams(paras[i])
        b = _char_ngrams(paras[i + 1])
        if not a or not b:
            continue
        j = len(a & b) / max(1, len(a | b))
        if j >= 0.28:
            similar_pairs += 1
    if similar_pairs:
        issues.append(
            {
                "severity": "warning",
                "code": "repetition_similar_paragraphs",
                "message": f"adjacent similar paragraph pairs: {similar_pairs}",
            }
        )

    return {
        "issues": issues,
        "repeated_lines_top": [{"count": c, "line": ln} for c, ln in repeated[:5]],
        "similar_paragraph_pairs": similar_pairs,
    }


def quality_score(*, atext_errors: int, atext_warnings: int, rep: dict[str, Any]) -> int:
    if atext_errors > 0:
        return 0
    score = 100
    score -= min(40, atext_warnings * 6)
    score -= min(30, int(rep.get("similar_paragraph_pairs") or 0) * 10)
    rep_issues = rep.get("issues") if isinstance(rep.get("issues"), list) else []
    rep_err = sum(1 for it in rep_issues if isinstance(it, dict) and it.get("severity") == "error")
    rep_warn = sum(1 for it in rep_issues if isinstance(it, dict) and it.get("severity") == "warning")
    score -= min(20, rep_err * 12)
    score -= min(20, rep_warn * 4)
    return max(0, int(score))


def scan_unposted(
    *,
    channels: Optional[set[str]],
    include_unvalidated: bool,
    include_posted: bool,
) -> dict[str, Any]:
    plan = load_planning_index()

    rows: list[dict[str, Any]] = []
    hard_fail_decisions: list[dict[str, Any]] = []

    root = script_data_root()
    for status_path in sorted(root.glob("CH*/[0-9][0-9][0-9]/status.json")):
        try:
            st = json.loads(status_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(st, dict):
            continue

        channel = _normalize_channel(str(st.get("channel") or st.get("channel_code") or status_path.parent.parent.name))
        video = _normalize_video(str(st.get("video_number") or st.get("video") or status_path.parent.name))
        if channels and channel not in channels:
            continue

        meta = st.get("metadata") if isinstance(st.get("metadata"), dict) else {}
        published_lock = bool(meta.get("published_lock"))

        script_id = str(st.get("script_id") or f"{channel}-{video}").strip() or f"{channel}-{video}"
        plan_row = plan.get(script_id)
        posted = published_lock or (bool(plan_row.posted) if plan_row else False)
        if posted and not include_posted:
            continue

        stages = st.get("stages") if isinstance(st.get("stages"), dict) else {}
        sv = stages.get("script_validation") if isinstance(stages.get("script_validation"), dict) else {}
        sv_status = str(sv.get("status") or "").strip()
        validated = sv_status == "completed"
        if not include_unvalidated and not validated:
            continue

        base = status_path.parent
        a_path = _canonical_a_text_path(base)
        text = _read_text_best_effort(a_path) if a_path.exists() else ""

        issues, stats = validate_a_text(text, meta)
        err = [it for it in issues if str((it or {}).get("severity") or "error").lower() != "warning"]
        warn = [it for it in issues if str((it or {}).get("severity") or "error").lower() == "warning"]

        rep = repetition_metrics(text) if text else {"issues": [], "repeated_lines_top": [], "similar_paragraph_pairs": 0}
        score = quality_score(atext_errors=len(err), atext_warnings=len(warn), rep=rep)

        if err:
            codes = []
            for it in err:
                if isinstance(it, dict) and it.get("code"):
                    codes.append(str(it.get("code")))
            # Deduplicate while preserving order.
            seen = set()
            reasons = []
            for c in codes:
                if c in seen:
                    continue
                seen.add(c)
                reasons.append(c)
            hard_fail_decisions.append(
                {
                    "channel": channel,
                    "video": video,
                    "verdict": "fail",
                    "reasons": reasons[:10],
                    "note": "hard_fail: " + ", ".join(reasons[:10]),
                }
            )

        rows.append(
            {
                "schema": "ytm.a_text_quality_scan_row.v1",
                "script_id": script_id,
                "channel": channel,
                "video": video,
                "status_path": str(status_path.relative_to(repo_root())),
                "a_text_path": str(a_path.relative_to(repo_root())) if a_path.exists() else str(a_path),
                "validated": validated,
                "a_text_stats": stats,
                "counts": {"errors": len(err), "warnings": len(warn)},
                "error_codes": [it.get("code") for it in err if isinstance(it, dict)],
                "warning_codes": [it.get("code") for it in warn if isinstance(it, dict)],
                "repetition": rep,
                "quality_score": score,
                "signals": {
                    "deleted_by_human": bool(meta.get("deleted_by_human")),
                    "skip_script_regeneration": bool(meta.get("skip_script_regeneration") or meta.get("skip_script_generation")),
                    "redo_script": meta.get("redo_script"),
                },
            }
        )

    rows.sort(key=lambda r: (int(r.get("quality_score") or 0), int((r.get("counts") or {}).get("errors") or 0)), reverse=False)

    counts = Counter()
    for r in rows:
        c = r.get("counts") if isinstance(r.get("counts"), dict) else {}
        counts["scripts_total"] += 1
        if int(c.get("errors") or 0) > 0:
            counts["hard_fail"] += 1
        if bool(r.get("validated")):
            counts["validated"] += 1

    return {
        "schema": "ytm.a_text_quality_scan.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filters": {
            "channels": sorted(channels) if channels else None,
            "include_unvalidated": include_unvalidated,
            "include_posted": include_posted,
        },
        "counts": dict(counts),
        "rows": rows,
        "hard_fail_decisions": hard_fail_decisions,
    }


def write_report(payload: dict[str, Any], label: str, *, write_latest: bool) -> tuple[Path, Path]:
    out_dir = logs_root() / "regression" / "a_text_quality_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = _utc_now_compact()
    json_path = out_dir / f"a_text_quality_scan_{label}__{ts}.json"
    md_path = out_dir / f"a_text_quality_scan_{label}__{ts}.md"

    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    counts = payload.get("counts") if isinstance(payload, dict) else {}
    rows = payload.get("rows") if isinstance(payload, dict) else []
    decisions = payload.get("hard_fail_decisions") if isinstance(payload, dict) else []

    lines: list[str] = []
    lines.append(f"# a_text_quality_scan report: {label}")
    lines.append("")
    lines.append(f"- generated_at: {payload.get('generated_at')}")
    lines.append(f"- filters: {json.dumps(payload.get('filters'), ensure_ascii=False)}")
    lines.append(f"- counts: {json.dumps(counts, ensure_ascii=False)}")
    lines.append(f"- hard_fail_decisions: {len(decisions) if isinstance(decisions, list) else 0}")
    lines.append("")

    # Worst 40 by score
    lines.append("## Lowest scores (first 40)")
    if isinstance(rows, list):
        for r in rows[:40]:
            if not isinstance(r, dict):
                continue
            ch = r.get("channel")
            vid = r.get("video")
            score = r.get("quality_score")
            c = r.get("counts") if isinstance(r.get("counts"), dict) else {}
            errs = c.get("errors")
            warns = c.get("warnings")
            codes = r.get("error_codes") if isinstance(r.get("error_codes"), list) else []
            codes_s = ", ".join([str(x) for x in codes[:6] if x]) if codes else ""
            lines.append(f"- {ch}-{vid}: score={score} errors={errs} warnings={warns} {codes_s}")

    md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    if write_latest:
        latest_json = out_dir / f"a_text_quality_scan_{label}__latest.json"
        latest_md = out_dir / f"a_text_quality_scan_{label}__latest.md"
        latest_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        latest_md.write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")

    return json_path, md_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Batch scan A-text quality for unposted episodes (no LLM).")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true", help="Scan all channels")
    g.add_argument("--channels", default="", help="Comma-separated channels (e.g., CH13,CH14)")
    ap.add_argument("--include-unvalidated", action="store_true", help="Include episodes without script_validation=completed")
    ap.add_argument("--include-posted", action="store_true", help="Include posted episodes (default: unposted only)")
    ap.add_argument("--write-latest", action="store_true", help="Also write *_latest.json/md (overwrite)")
    ap.add_argument("--label", default="all", help="Label for output filenames (default: all)")
    ap.add_argument("--write-decisions", action="store_true", help="Also write hard-fail decisions JSONL under the report dir")
    args = ap.parse_args(argv)

    channels = None
    if not args.all:
        channels = {_normalize_channel(x.strip()) for x in str(args.channels).split(",") if x.strip()}
        args.label = args.label or "selected"

    payload = scan_unposted(
        channels=channels,
        include_unvalidated=bool(args.include_unvalidated),
        include_posted=bool(args.include_posted),
    )
    json_path, md_path = write_report(payload, args.label, write_latest=bool(args.write_latest))

    decisions_path = None
    if bool(args.write_decisions):
        out_dir = json_path.parent
        decisions_path = out_dir / f"a_text_quality_scan_{args.label}__{json_path.stem.split('__')[-1]}.decisions.jsonl"
        decisions = payload.get("hard_fail_decisions") if isinstance(payload, dict) else None
        if isinstance(decisions, list) and decisions:
            decisions_path.write_text(
                "\n".join(json.dumps(d, ensure_ascii=False) for d in decisions) + "\n",
                encoding="utf-8",
            )
        else:
            decisions_path.write_text("", encoding="utf-8")

    print(
        json.dumps(
            {
                "ok": True,
                "json": str(json_path.relative_to(repo_root())),
                "md": str(md_path.relative_to(repo_root())),
                "decisions": str(decisions_path.relative_to(repo_root())) if decisions_path else None,
                "counts": payload.get("counts"),
            },
            ensure_ascii=False,
        )
    )
    # Exit non-zero only when hard failures exist (used by CI/ops). Warnings do not fail the scan.
    return 0 if int((payload.get("counts") or {}).get("hard_fail", 0) or 0) == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

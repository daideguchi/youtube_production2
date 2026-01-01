#!/usr/bin/env python3
"""
Dialog-AI script audit helper (NO LLM API).

This tool never calls any LLM routers. It only:
- enumerates episodes (status.json + planning CSV),
- writes scan reports,
- applies/updates audit metadata + redo flags in status.json.

SSOT: ssot/ops/OPS_DIALOG_AI_SCRIPT_AUDIT.md
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from factory_common import locks as coord_locks
from factory_common import paths as repo_paths


SCRIPT_ID_RE = re.compile(r"\bCH\d{2}-\d{3}\b")
SCRIPT_ID_PARTS_RE = re.compile(r"^(CH\d{2})-(\d{3})$")


def parse_script_id_parts(script_id: str) -> tuple[Optional[str], Optional[str]]:
    s = str(script_id or "").strip()
    m = SCRIPT_ID_PARTS_RE.match(s)
    if not m:
        return None, None
    return m.group(1), m.group(2)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sha1_text(text: str) -> str:
    h = hashlib.sha1()
    h.update(text.encode("utf-8", errors="ignore"))
    return h.hexdigest()


def read_text_best_effort(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")


def atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def list_status_paths() -> list[Path]:
    root = repo_paths.script_data_root()
    return sorted(root.glob("CH*/[0-9][0-9][0-9]/status.json"))


def normalize_video_number(raw: str) -> str:
    return str(raw).zfill(3)


def normalize_channel_code(raw: str) -> str:
    return str(raw).upper()


def maybe_normalize_video_number(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if not re.fullmatch(r"\d{1,3}", s):
        return None
    return normalize_video_number(s)


def extract_script_id_from_row(joined: str) -> Optional[str]:
    m = SCRIPT_ID_RE.search(joined or "")
    return m.group(0) if m else None


@dataclass(frozen=True)
class PlanningRow:
    script_id: str
    channel: str
    video: str
    title: str
    intent: str
    audience: str
    outline_notes: str
    raw_joined: str
    posted: bool


def load_planning_index() -> dict[str, PlanningRow]:
    """
    Best-effort Planning CSV index across all channels.
    Keyed by script_id (e.g., CH15-021).
    """
    out: dict[str, PlanningRow] = {}
    planning_dir = repo_paths.planning_channels_dir()
    for csv_path in sorted(planning_dir.glob("CH*.csv")):
        channel_guess = normalize_channel_code(csv_path.stem)
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

            script_id = extract_script_id_from_row(joined)
            script_ch, script_video = parse_script_id_parts(script_id or "")
            ch = str(col(row, "チャンネル") or channel_guess).strip() or channel_guess
            ch = normalize_channel_code(script_ch or ch)

            video_raw = col(row, "動画番号") or col(row, "No.") or col(row, "VideoNumber")
            video = maybe_normalize_video_number(video_raw) or script_video

            if not script_id and ch and video and SCRIPT_ID_RE.match(f"{ch}-{video}"):
                script_id = f"{ch}-{video}"
            if not script_id:
                continue
            if not video:
                # Prefer deriving video from script_id, else skip the row.
                _, inferred_video = parse_script_id_parts(script_id)
                video = inferred_video
            if not video:
                continue

            posted = ("投稿済み" in joined) or ("公開済み" in joined)

            title = col(row, "タイトル").strip()
            intent = col(row, "企画意図").strip()
            audience = col(row, "ターゲット層").strip()
            outline_notes = col(row, "具体的な内容（話の構成案）").strip()

            out[script_id] = PlanningRow(
                script_id=script_id,
                channel=ch,
                video=video,
                title=title,
                intent=intent,
                audience=audience,
                outline_notes=outline_notes,
                raw_joined=joined,
                posted=posted,
            )
    return out


def is_posted_episode(status_obj: dict[str, Any], planning_row: Optional[PlanningRow]) -> bool:
    meta = status_obj.get("metadata") if isinstance(status_obj.get("metadata"), dict) else {}
    if bool(meta.get("published_lock")):
        return True
    if planning_row and planning_row.posted:
        return True
    return False


def find_status(channel: str, video: str) -> Path:
    return repo_paths.status_path(channel, video)


def assembled_path_for_episode(channel: str, video: str) -> Path:
    base = repo_paths.video_root(channel, video)
    content = base / "content"
    # Prefer assembled_human when present, else assembled.
    if (content / "assembled_human.md").exists():
        return content / "assembled_human.md"
    return content / "assembled.md"


def safe_excerpt(text: str, *, max_chars: int = 140) -> str:
    s = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 1].rstrip() + "…"


def head_excerpt(text: str, *, max_chars: int = 140) -> str:
    return safe_excerpt(text, max_chars=max_chars)


def tail_excerpt(text: str, *, max_chars: int = 140) -> str:
    s = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) <= max_chars:
        return s
    return "…" + s[-(max_chars - 1) :].lstrip()


def load_status_obj(path: Path) -> dict[str, Any]:
    return json.loads(read_text_best_effort(path))


def save_status_obj(path: Path, obj: dict[str, Any]) -> None:
    atomic_write_json(path, obj)


def scan_validated_not_posted(
    *,
    channels: Optional[set[str]] = None,
    include_unvalidated: bool = False,
    include_posted: bool = False,
    include_locked: bool = False,
) -> list[dict[str, Any]]:
    planning = load_planning_index()
    active_locks = coord_locks.default_active_locks_for_mutation()

    out: list[dict[str, Any]] = []
    for status_path in list_status_paths():
        st = load_status_obj(status_path)
        script_id = str(st.get("script_id") or "").strip()
        script_ch, script_video = parse_script_id_parts(script_id)

        channel = normalize_channel_code(str(st.get("channel") or st.get("channel_code") or "").strip())
        if not channel:
            channel = normalize_channel_code(script_ch or "")
        if not channel:
            channel = normalize_channel_code(status_path.parent.parent.name)

        video = (
            maybe_normalize_video_number(st.get("video_number"))
            or maybe_normalize_video_number(st.get("video"))
            or maybe_normalize_video_number(st.get("video_id"))
            or script_video
            or status_path.parent.name
        )
        if not channel or not video:
            continue

        if channels and channel not in channels:
            continue

        plan_row = planning.get(script_id)
        posted = is_posted_episode(st, plan_row)
        if posted and not include_posted:
            continue

        stages = st.get("stages") if isinstance(st.get("stages"), dict) else {}
        sv = stages.get("script_validation") if isinstance(stages.get("script_validation"), dict) else {}
        sv_status = str(sv.get("status") or "").strip() or None
        validated = sv_status == "completed"
        if not include_unvalidated and not validated:
            continue

        blocking = coord_locks.find_blocking_lock(status_path, active_locks)
        if blocking and not include_locked:
            continue

        details = sv.get("details") if isinstance(sv.get("details"), dict) else {}
        stats = details.get("stats") if isinstance(details.get("stats"), dict) else {}
        llm_gate = details.get("llm_quality_gate") if isinstance(details.get("llm_quality_gate"), dict) else {}
        sem_gate = details.get("semantic_alignment_gate") if isinstance(details.get("semantic_alignment_gate"), dict) else {}

        meta = st.get("metadata") if isinstance(st.get("metadata"), dict) else {}
        redo_script = meta.get("redo_script")
        redo_note = str(meta.get("redo_note") or "").strip()
        dialog_audit = meta.get("dialog_ai_audit") if isinstance(meta.get("dialog_ai_audit"), dict) else {}

        assembled_path = assembled_path_for_episode(channel, video)
        assembled_text = read_text_best_effort(assembled_path) if assembled_path.exists() else ""
        assembled_sha1 = sha1_text(assembled_text) if assembled_text else ""

        out.append(
            {
                "schema": "ytm.dialog_ai_script_audit_scan_row.v1",
                "script_id": script_id or f"{channel}-{video}",
                "channel": channel,
                "video": video,
                "status_path": status_path.as_posix(),
                "assembled_path": assembled_path.as_posix(),
                "validated": validated,
                "script_validation_status": sv_status,
                "top_status": str(st.get("status") or "").strip() or None,
                "redo_script": redo_script,
                "redo_note": redo_note,
                "signals": {
                    "has_auto_length_fix": isinstance(details.get("auto_length_fix"), dict),
                    "has_deterministic_cleanup": isinstance(details.get("deterministic_cleanup"), dict),
                    "llm_quality_gate_verdict": str(llm_gate.get("verdict") or "").strip() or None,
                    "semantic_alignment_verdict": str(sem_gate.get("verdict") or "").strip() or None,
                    "has_dialog_audit": bool(dialog_audit),
                    "dialog_audit_verdict": str(dialog_audit.get("verdict") or "").strip() or None,
                    "dialog_audit_hash_match": (
                        bool(dialog_audit.get("script_hash_sha1"))
                        and bool(assembled_sha1)
                        and str(dialog_audit.get("script_hash_sha1")) == assembled_sha1
                    ),
                    "blocked_by_lock": (
                        {
                            "lock_id": blocking.lock_id,
                            "created_by": blocking.created_by,
                            "mode": blocking.mode,
                        }
                        if blocking
                        else None
                    ),
                },
                "stats": {
                    "char_count": stats.get("char_count"),
                    "pause_lines": stats.get("pause_lines"),
                    "target_min": stats.get("target_chars_min"),
                    "target_max": stats.get("target_chars_max"),
                },
                "planning": {
                    "posted": bool(plan_row.posted) if plan_row else None,
                    "title": (plan_row.title if plan_row else ""),
                    "intent": (plan_row.intent if plan_row else ""),
                    "audience": (plan_row.audience if plan_row else ""),
                    "outline_notes": (plan_row.outline_notes if plan_row else ""),
                },
                "script_hash_sha1": assembled_sha1,
                "excerpts": {
                    "head": head_excerpt(assembled_text, max_chars=140) if assembled_text else "",
                    "tail": tail_excerpt(assembled_text, max_chars=140) if assembled_text else "",
                },
            }
        )

    return out


def write_scan_report(rows: list[dict[str, Any]], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "scan_rows.json"
    md_path = out_dir / "scan_summary.md"

    atomic_write_json(json_path, {"schema": "ytm.dialog_ai_script_audit_scan.v1", "generated_at": utc_now_iso(), "rows": rows})

    # Summaries (keep markdown short)
    total = len(rows)
    validated = sum(1 for r in rows if r.get("validated"))
    redo_false = sum(1 for r in rows if r.get("redo_script") is False)
    redo_true = sum(1 for r in rows if r.get("redo_script") is True)
    redo_none = total - redo_false - redo_true
    blocked = sum(1 for r in rows if (r.get("signals") or {}).get("blocked_by_lock"))
    has_auto = sum(1 for r in rows if (r.get("signals") or {}).get("has_auto_length_fix"))
    has_det = sum(1 for r in rows if (r.get("signals") or {}).get("has_deterministic_cleanup"))
    has_note = sum(1 for r in rows if str(r.get("redo_note") or "").strip())

    lines = []
    lines.append("# dialog_ai_script_audit scan summary\n")
    lines.append(f"- generated_at: {utc_now_iso()}\n")
    lines.append(f"- total_rows: {total}\n")
    lines.append(f"- validated_rows: {validated}\n")
    lines.append(f"- redo_script: false={redo_false} true={redo_true} unset={redo_none}\n")
    lines.append(f"- blocked_by_lock: {blocked}\n")
    lines.append(f"- signals: auto_length_fix={has_auto} deterministic_cleanup={has_det} redo_note_present={has_note}\n")
    lines.append("\n")
    lines.append("SSOT: ssot/ops/OPS_DIALOG_AI_SCRIPT_AUDIT.md\n")

    md_path.write_text("".join(lines), encoding="utf-8")
    return {"json": json_path.as_posix(), "md": md_path.as_posix()}


def _parse_reason_list(raw: str) -> list[str]:
    if not raw:
        return []
    parts = []
    for p in re.split(r"[,\n]", raw):
        s = str(p).strip()
        if s:
            parts.append(s)
    # dedupe preserve order
    seen = set()
    out = []
    for p in parts:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def apply_audit_mark(
    *,
    channel: str,
    video: str,
    verdict: str,
    reasons: list[str],
    note: str,
    audited_by: str,
    dry_run: bool,
) -> dict[str, Any]:
    channel = normalize_channel_code(channel)
    video = normalize_video_number(video)
    status_path = find_status(channel, video)

    active_locks = coord_locks.default_active_locks_for_mutation()
    blocking = coord_locks.find_blocking_lock(status_path, active_locks)
    if blocking:
        return {
            "ok": False,
            "channel": channel,
            "video": video,
            "status_path": status_path.as_posix(),
            "error": "blocked_by_lock",
            "lock": {"id": blocking.lock_id, "created_by": blocking.created_by, "mode": blocking.mode},
        }

    st = load_status_obj(status_path)
    meta = st.get("metadata") if isinstance(st.get("metadata"), dict) else {}

    planning = load_planning_index()
    plan_row = planning.get(str(st.get("script_id") or f"{channel}-{video}"))

    assembled_path = assembled_path_for_episode(channel, video)
    assembled_text = read_text_best_effort(assembled_path) if assembled_path.exists() else ""
    assembled_sha1 = sha1_text(assembled_text) if assembled_text else ""

    v = (verdict or "").strip().lower()
    if v not in {"pass", "fail", "grey"}:
        raise ValueError("verdict must be pass|fail|grey")

    redo_script = False if v == "pass" else True
    meta["redo_script"] = redo_script
    if redo_script:
        meta["redo_audio"] = True

    # Normalize redo_note: keep it short and actionable.
    note = str(note or "").strip()
    if v == "pass":
        # Pass: `redo_note` is for "要対応" only. Keep pass commentary in `dialog_ai_audit.notes`.
        meta.pop("redo_note", None)
    else:
        # Fail/grey: always write a note (reason codes + note)
        reason_txt = ", ".join(reasons) if reasons else ""
        parts = []
        if reason_txt:
            parts.append(reason_txt)
        if note:
            parts.append(note)
        meta["redo_note"] = " / ".join([p for p in parts if p]).strip() or "要対応"

    meta["dialog_ai_audit"] = {
        "schema": "ytm.dialog_ai_script_audit.v1",
        "audited_at": utc_now_iso(),
        "audited_by": audited_by,
        "verdict": v,
        "reasons": reasons,
        "notes": note,
        "script_hash_sha1": assembled_sha1,
        "planning_snapshot": {
            "title": (plan_row.title if plan_row else ""),
            "intent": (plan_row.intent if plan_row else ""),
            "audience": (plan_row.audience if plan_row else ""),
            "outline_notes": (plan_row.outline_notes if plan_row else ""),
        },
    }

    st["metadata"] = meta

    if not dry_run:
        save_status_obj(status_path, st)

    return {
        "ok": True,
        "dry_run": dry_run,
        "channel": channel,
        "video": video,
        "status_path": status_path.as_posix(),
        "redo_script": redo_script,
        "script_hash_sha1": assembled_sha1,
    }


def cmd_scan(args: argparse.Namespace) -> int:
    channels = None
    if args.channels:
        channels = {normalize_channel_code(x.strip()) for x in args.channels.split(",") if x.strip()}
    rows = scan_validated_not_posted(
        channels=channels,
        include_unvalidated=args.include_unvalidated,
        include_posted=args.include_posted,
        include_locked=args.include_locked,
    )

    out_dir = repo_paths.script_data_root() / "_reports" / "dialog_ai_script_audit" / utc_now_compact()
    paths = write_scan_report(rows, out_dir)
    print(json.dumps({"ok": True, "out_dir": out_dir.as_posix(), "paths": paths, "rows": len(rows)}, ensure_ascii=False))
    return 0


def cmd_mark(args: argparse.Namespace) -> int:
    res = apply_audit_mark(
        channel=args.channel,
        video=args.video,
        verdict=args.verdict,
        reasons=_parse_reason_list(args.reasons or ""),
        note=args.note or "",
        audited_by=str(args.audited_by or "").strip() or (os.getenv("LLM_AGENT_NAME") or "dialog_ai"),
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res.get("ok") else 2


def cmd_mark_batch(args: argparse.Namespace) -> int:
    decisions_path = Path(args.decisions)
    if not decisions_path.exists():
        raise FileNotFoundError(decisions_path.as_posix())

    audited_by = str(args.audited_by or "").strip() or (os.getenv("LLM_AGENT_NAME") or "dialog_ai")
    dry_run = bool(args.dry_run)

    ok = 0
    skipped = 0
    failed = 0

    for line in read_text_best_effort(decisions_path).splitlines():
        s = line.strip()
        if not s:
            continue
        obj = json.loads(s)
        channel = obj.get("channel")
        video = obj.get("video")
        verdict = obj.get("verdict")
        reasons = obj.get("reasons") if isinstance(obj.get("reasons"), list) else _parse_reason_list(str(obj.get("reasons") or ""))
        note = str(obj.get("note") or "").strip()
        if not channel or not video or not verdict:
            skipped += 1
            continue
        res = apply_audit_mark(
            channel=str(channel),
            video=str(video),
            verdict=str(verdict),
            reasons=[str(x) for x in reasons if str(x).strip()],
            note=note,
            audited_by=audited_by,
            dry_run=dry_run,
        )
        if res.get("ok"):
            ok += 1
        else:
            failed += 1

    print(
        json.dumps(
            {"ok": True, "dry_run": dry_run, "applied": ok, "skipped": skipped, "failed": failed},
            ensure_ascii=False,
        )
    )
    return 0 if failed == 0 else 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Dialog-AI script audit helper (no LLM API)")
    sub = p.add_subparsers(dest="cmd", required=True)

    scan = sub.add_parser("scan", help="scan episodes and write a local report (no LLM)")
    scan.add_argument("--channels", default="", help="comma-separated channels (e.g., CH02,CH03)")
    scan.add_argument("--include-unvalidated", action="store_true", help="include episodes without script_validation=completed")
    scan.add_argument("--include-posted", action="store_true", help="include posted/published_lock episodes")
    scan.add_argument("--include-locked", action="store_true", help="include paths blocked by active coordination locks")
    scan.set_defaults(func=cmd_scan)

    mark = sub.add_parser("mark", help="apply a single audit verdict to status.json (no LLM)")
    mark.add_argument("--channel", required=True)
    mark.add_argument("--video", required=True)
    mark.add_argument("--verdict", required=True, choices=["pass", "fail", "grey"])
    mark.add_argument("--reasons", default="", help="comma-separated reason codes")
    mark.add_argument("--note", default="", help="human-readable note (short, actionable)")
    mark.add_argument("--audited-by", default="", help="auditor id (default: env LLM_AGENT_NAME)")
    mark.add_argument("--dry-run", action="store_true")
    mark.set_defaults(func=cmd_mark)

    batch = sub.add_parser("mark-batch", help="apply audit verdicts from a JSONL file")
    batch.add_argument("--decisions", required=True, help="path to JSONL decisions file")
    batch.add_argument("--audited-by", default="", help="auditor id (default: env LLM_AGENT_NAME)")
    batch.add_argument("--dry-run", action="store_true")
    batch.set_defaults(func=cmd_mark_batch)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())

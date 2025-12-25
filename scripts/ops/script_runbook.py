#!/usr/bin/env python3
from __future__ import annotations

"""
script_runbook.py — Script factory ops helper (fixed entrypoint)

This is a thin wrapper around `script_pipeline.runner` that standardizes:
  - new: write a new script from scratch (until script_validation)
  - redo-full: reset and regenerate from scratch (until script_validation)
  - resume: continue from the current pending stage(s) (until script_validation)
  - rewrite: user-instruction rewrite (instruction required; then script_validation)

Recommended invocation (loads .env + PYTHONPATH):
  ./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/script_runbook.py ...

Examples:
  # New script (CH10 example)
  ./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/script_runbook.py new --channel CH10 --video 004

  # Redo full (CH07-019..030): regenerate from scratch (expensive)
  ./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/script_runbook.py redo-full --channel CH07 --from 019 --to 030

  # Resume from mid-pipeline (single episode)
  ./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/script_runbook.py resume --channel CH07 --video 019

  # Rewrite (instruction required)
  ./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/script_runbook.py rewrite --channel CH07 --video 019 --instruction "言い回しをもっと理解しやすい表現に"
"""

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from scripts.ops._bootstrap import bootstrap

bootstrap(load_env=True)

from factory_common.paths import logs_root  # noqa: E402
from factory_common.llm_router import get_router  # noqa: E402
from script_pipeline.runner import reconcile_status, reset_video, run_next, run_stage  # noqa: E402
from script_pipeline.sot import load_status, save_status, status_path  # noqa: E402
from script_pipeline.validator import validate_a_text  # noqa: E402


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _norm_channel(value: str) -> str:
    return str(value or "").strip().upper()


def _norm_video(value: str) -> str:
    raw = str(value or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        raise SystemExit(f"invalid video: {value!r}")
    return f"{int(digits):03d}"


def _iter_range(start: str, end: str) -> Iterable[str]:
    a = int(_norm_video(start))
    b = int(_norm_video(end))
    if a > b:
        a, b = b, a
    for n in range(a, b + 1):
        yield f"{n:03d}"


def _safe_semantic_verdict(st) -> str:
    meta = getattr(st, "metadata", None) or {}
    sa = meta.get("semantic_alignment") if isinstance(meta, dict) else None
    if isinstance(sa, dict):
        v = str(sa.get("verdict") or "").strip().lower()
        if v:
            return v
    return ""


def _safe_planning_coherence(st) -> str:
    meta = getattr(st, "metadata", None) or {}
    pi = meta.get("planning_integrity") if isinstance(meta, dict) else None
    if isinstance(pi, dict):
        c = str(pi.get("coherence") or "").strip().lower()
        if c:
            return c
    return ""


def _safe_stage_status(st, stage: str) -> str:
    try:
        s = st.stages.get(stage)
    except Exception:
        s = None
    if s is None:
        return ""
    return str(getattr(s, "status", "") or "")


@dataclass(frozen=True)
class ItemResult:
    channel: str
    video: str
    mode: str
    ok: bool
    status: str
    script_validation_status: str
    semantic_verdict: str
    planning_coherence: str
    status_json: str
    note: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "channel": self.channel,
            "video": self.video,
            "mode": self.mode,
            "ok": self.ok,
            "status": self.status,
            "script_validation_status": self.script_validation_status,
            "semantic_verdict": self.semantic_verdict,
            "planning_coherence": self.planning_coherence,
            "status_json": self.status_json,
            "note": self.note,
        }


def _extract_llm_text_content(result: Dict[str, Any]) -> str:
    content = result.get("content")
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text", "")).strip())
        return " ".join([p for p in parts if p]).strip()
    return str(content or "").strip()


def _canonical_paths(channel: str, video: str) -> tuple[Path, Path]:
    base = status_path(channel, video).parent
    human_path = base / "content" / "assembled_human.md"
    mirror_path = base / "content" / "assembled.md"
    return human_path, mirror_path


def _run_until(channel: str, video: str, *, until_stage: str, max_iter: int) -> None:
    """
    Run pending stages (via run_next) until:
    - until_stage is completed, or
    - no progress is observed, or
    - max_iter is reached.
    """
    until = str(until_stage or "").strip()
    if not until:
        raise SystemExit("until_stage is required")

    last_pending: Optional[List[str]] = None
    for _ in range(max(1, int(max_iter))):
        # Bootstrap for `new`: create status.json by executing the first stage once.
        if not status_path(channel, video).exists():
            run_next(channel, video, title=None)
            continue

        st = load_status(channel, video)
        if _safe_stage_status(st, until) == "completed":
            return

        pending = [name for name, s in st.stages.items() if s.status != "completed"]
        if not pending:
            return
        if pending == last_pending:
            return
        last_pending = pending

        run_next(channel, video, title=None)


def _result_for(channel: str, video: str, mode: str, note: str = "") -> ItemResult:
    st = load_status(channel, video)
    return ItemResult(
        channel=channel,
        video=video,
        mode=mode,
        ok=(_safe_stage_status(st, "script_validation") == "completed"),
        status=str(st.status or ""),
        script_validation_status=_safe_stage_status(st, "script_validation"),
        semantic_verdict=_safe_semantic_verdict(st),
        planning_coherence=_safe_planning_coherence(st),
        status_json=str(status_path(channel, video)),
        note=note,
    )


def cmd_new(args: argparse.Namespace) -> int:
    ch = _norm_channel(args.channel)
    no = _norm_video(args.video)
    max_iter = int(args.max_iter)
    until = str(args.until)

    _run_until(ch, no, until_stage=until, max_iter=max_iter)
    res = _result_for(ch, no, mode="new")
    print(json.dumps(res.as_dict(), ensure_ascii=False, indent=2))
    return 0 if res.ok else 2


def cmd_redo(args: argparse.Namespace) -> int:
    ch = _norm_channel(args.channel)
    mode = str(args.mode)
    max_iter = int(args.max_iter)
    until = str(args.until)

    items: List[ItemResult] = []
    for no in _iter_range(args.from_video, args.to_video):
        note = ""
        try:
            if mode == "validate":
                run_stage(ch, no, "script_validation", title=None)
                note = "validated"
            elif mode == "regenerate":
                reset_video(ch, no, wipe_research=bool(args.wipe_research))
                _run_until(ch, no, until_stage=until, max_iter=max_iter)
                note = "reset+run_all"
            else:
                raise SystemExit(f"unknown mode: {mode}")
        except SystemExit as exc:
            note = f"system_exit: {exc}"
        except Exception as exc:
            note = f"exception: {exc}"

        if status_path(ch, no).exists():
            items.append(_result_for(ch, no, mode=f"redo:{mode}", note=note))
        else:
            items.append(
                ItemResult(
                    channel=ch,
                    video=no,
                    mode=f"redo:{mode}",
                    ok=False,
                    status="",
                    script_validation_status="",
                    semantic_verdict="",
                    planning_coherence="",
                    status_json=str(status_path(ch, no)),
                    note=f"status_missing ({note})",
                )
            )

    payload = {
        "generated_at": _utc_now_compact(),
        "channel": ch,
        "mode": mode,
        "range": {"from": _norm_video(args.from_video), "to": _norm_video(args.to_video)},
        "items": [it.as_dict() for it in items],
        "counts": {
            "total": len(items),
            "ok": sum(1 for it in items if it.ok),
            "failed": sum(1 for it in items if not it.ok),
        },
    }

    out_dir = logs_root() / "ops" / "script_runbook"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"redo_{ch}_{_utc_now_compact()}_{mode}_{payload['range']['from']}-{payload['range']['to']}.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({**payload, "report_path": str(out_path)}, ensure_ascii=False, indent=2))
    return 0 if payload["counts"]["failed"] == 0 else 2


def cmd_redo_full(args: argparse.Namespace) -> int:
    # Alias: fully regenerate (reset + run until target stage).
    args.mode = "regenerate"
    return cmd_redo(args)


def cmd_resume(args: argparse.Namespace) -> int:
    ch = _norm_channel(args.channel)
    no = _norm_video(args.video)
    max_iter = int(args.max_iter)
    until = str(args.until)

    if not status_path(ch, no).exists():
        raise SystemExit(f"status_missing: {status_path(ch, no)} (use: new / redo-full)")

    if bool(args.reconcile):
        reconcile_status(ch, no, allow_downgrade=bool(args.allow_downgrade))

    # Safety: resuming to `script_validation` should not implicitly regenerate earlier stages
    # (those stages may be intentionally purged while A-text already exists).
    st = load_status(ch, no)
    if _safe_stage_status(st, until) != "completed":
        if until in {"script_validation", "audio_synthesis"}:
            human_path, mirror_path = _canonical_paths(ch, no)
            if not (human_path.exists() or mirror_path.exists()):
                # No SoT A-text to validate yet → run upstream stages first.
                _run_until(ch, no, until_stage=until, max_iter=max_iter)
            else:
                run_stage(ch, no, until, title=None)
        else:
            _run_until(ch, no, until_stage=until, max_iter=max_iter)

    res = _result_for(ch, no, mode="resume")
    print(json.dumps(res.as_dict(), ensure_ascii=False, indent=2))
    return 0 if res.ok else 2


def _rewrite_prompt(
    *,
    title: str,
    thumb_top: str,
    thumb_bottom: str,
    instruction: str,
    segment_text: str,
) -> str:
    return (
        "あなたはYouTube台本（Aテキスト）のリライト担当です。\n"
        "目的: 指示に従って言い回しを改善するが、企画の主題（タイトル/サムネの訴求）から逸脱しない。\n\n"
        "【企画】\n"
        f"- タイトル: {title}\n"
        f"- サムネ上: {thumb_top}\n"
        f"- サムネ下: {thumb_bottom}\n\n"
        "【ユーザー指示（必須）】\n"
        f"{instruction}\n\n"
        "【絶対ルール】\n"
        "- 出力はリライト後の本文のみ（前置き/解説/箇条書き/番号リスト/見出し/メタ言及は禁止）。\n"
        "- 意味（主張/論旨）は維持し、主題がズレる追記・別テーマへの寄り道は禁止。\n"
        "- 長さを大きく変えない（削って短くしない）。\n"
        "- `---` は入れない（区切りは別工程で扱う）。\n\n"
        "【入力本文】\n"
        f"{segment_text.strip()}\n"
    )


def _split_sections(text: str) -> List[str]:
    # Split by the only allowed pause marker (context-based, not mechanical).
    parts = re.split(r"(?m)^\\s*---\\s*$", (text or "").replace("\r\n", "\n").replace("\r", "\n").strip())
    return [p.strip("\n") for p in parts]


def _join_sections(sections: List[str]) -> str:
    cleaned = [str(s or "").strip("\n") for s in sections]
    out = "\n\n---\n\n".join(cleaned).strip() + "\n"
    return out


def cmd_rewrite(args: argparse.Namespace) -> int:
    ch = _norm_channel(args.channel)
    no = _norm_video(args.video)
    max_iter = int(args.max_iter)
    until = str(args.until)

    instruction = str(args.instruction or "").strip()
    if not instruction and args.instruction_file:
        instruction = Path(str(args.instruction_file)).read_text(encoding="utf-8").strip()
    if not instruction:
        raise SystemExit("rewrite requires --instruction or --instruction-file")

    if not status_path(ch, no).exists():
        raise SystemExit(f"status_missing: {status_path(ch, no)} (use: new / redo-full)")

    st = load_status(ch, no)
    planning = (st.metadata or {}).get("planning") if isinstance(st.metadata, dict) else None
    if not isinstance(planning, dict):
        planning = {}
    title = str((st.metadata or {}).get("sheet_title") or (st.metadata or {}).get("expected_title") or (st.metadata or {}).get("title") or st.script_id).strip()
    thumb_top = str(planning.get("thumbnail_upper") or (st.metadata or {}).get("thumbnail_title_top") or "").strip()
    thumb_bottom = str(planning.get("thumbnail_lower") or (st.metadata or {}).get("thumbnail_title_bottom") or "").strip()

    human_path, mirror_path = _canonical_paths(ch, no)
    in_path = human_path if human_path.exists() else mirror_path
    if not in_path.exists():
        raise SystemExit(f"script_missing: {in_path} (nothing to rewrite)")

    src_text = in_path.read_text(encoding="utf-8")
    sections = _split_sections(src_text)
    if not sections:
        raise SystemExit("script is empty; nothing to rewrite")

    router = get_router()
    rewritten: List[str] = []
    for idx, seg in enumerate(sections, start=1):
        prompt = _rewrite_prompt(
            title=title,
            thumb_top=thumb_top,
            thumb_bottom=thumb_bottom,
            instruction=instruction,
            segment_text=seg,
        )
        prev_key = os.environ.get("LLM_ROUTING_KEY")
        os.environ["LLM_ROUTING_KEY"] = f"{ch}-{no}"
        try:
            result = router.call_with_raw(
                task="script_a_text_quality_fix",
                messages=[{"role": "user", "content": prompt}],
            )
        finally:
            if prev_key is None:
                os.environ.pop("LLM_ROUTING_KEY", None)
            else:
                os.environ["LLM_ROUTING_KEY"] = prev_key
        out = _extract_llm_text_content(result)
        if not out.strip():
            raise SystemExit(f"rewrite_failed: empty output (section {idx}/{len(sections)})")
        rewritten.append(out.strip("\n"))

    new_text = _join_sections(rewritten)

    issues, _stats = validate_a_text(new_text, st.metadata or {})
    hard = [it for it in issues if str((it or {}).get("severity") or "error").lower() != "warning"]
    if hard:
        raise SystemExit(f"rewrite_failed: deterministic_validation_failed ({len(hard)} issues)")

    # Backup then write canonical + mirror.
    backup_dir = human_path.parent / "analysis" / "rewrite"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"backup_{_utc_now_compact()}_{in_path.name}"
    backup_path.write_text(src_text, encoding="utf-8")

    human_path.parent.mkdir(parents=True, exist_ok=True)
    human_path.write_text(new_text, encoding="utf-8")
    mirror_path.write_text(new_text, encoding="utf-8")

    # Mark downstream as needing redo + force re-validation.
    st = load_status(ch, no)
    st.metadata["redo_audio"] = True
    if "script_validation" in st.stages:
        st.stages["script_validation"].status = "pending"
    if "audio_synthesis" in st.stages:
        st.stages["audio_synthesis"].status = "pending"
    st.status = "script_in_progress"
    save_status(st)

    reconcile_status(ch, no, allow_downgrade=False)
    _run_until(ch, no, until_stage=until, max_iter=max_iter)

    res = _result_for(ch, no, mode="rewrite", note=f"backup={backup_path}")
    print(json.dumps(res.as_dict(), ensure_ascii=False, indent=2))
    return 0 if res.ok else 2


def main() -> int:
    ap = argparse.ArgumentParser(description="Script pipeline runbook helper (new/redo).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    new_p = sub.add_parser("new", help="Create a new script by running pending stages until stable.")
    new_p.add_argument("--channel", required=True)
    new_p.add_argument("--video", required=True)
    new_p.add_argument("--until", default="script_validation", help="Stop when this stage is completed (default: script_validation).")
    new_p.add_argument("--max-iter", type=int, default=30)
    new_p.set_defaults(func=cmd_new)

    redo_p = sub.add_parser("redo", help="Redo existing scripts for a range (validate or regenerate).")
    redo_p.add_argument("--channel", required=True)
    redo_p.add_argument("--from", dest="from_video", required=True)
    redo_p.add_argument("--to", dest="to_video", required=True)
    redo_p.add_argument("--mode", choices=["validate", "regenerate"], default="validate")
    redo_p.add_argument("--wipe-research", action="store_true", help="When regenerating, also wipe research outputs.")
    redo_p.add_argument("--until", default="script_validation", help="(regenerate) stop when this stage is completed (default: script_validation).")
    redo_p.add_argument("--max-iter", type=int, default=30, help="Max stage executions per video (regenerate mode).")
    redo_p.set_defaults(func=cmd_redo)

    redo_full_p = sub.add_parser("redo-full", help="Reset + regenerate from scratch for a range (alias of redo --mode regenerate).")
    redo_full_p.add_argument("--channel", required=True)
    redo_full_p.add_argument("--from", dest="from_video", required=True)
    redo_full_p.add_argument("--to", dest="to_video", required=True)
    redo_full_p.add_argument("--wipe-research", action="store_true", help="Also wipe research outputs.")
    redo_full_p.add_argument("--until", default="script_validation", help="Stop when this stage is completed (default: script_validation).")
    redo_full_p.add_argument("--max-iter", type=int, default=30, help="Max stage executions per video.")
    redo_full_p.set_defaults(func=cmd_redo_full)

    resume_p = sub.add_parser("resume", help="Resume a single episode from current pending stages until target stage.")
    resume_p.add_argument("--channel", required=True)
    resume_p.add_argument("--video", required=True)
    resume_p.add_argument("--until", default="script_validation", help="Stop when this stage is completed (default: script_validation).")
    resume_p.add_argument("--max-iter", type=int, default=30)
    resume_p.add_argument(
        "--no-reconcile",
        dest="reconcile",
        action="store_false",
        help="Disable reconcile (by default, reconcile is performed before resuming).",
    )
    resume_p.set_defaults(reconcile=True)
    resume_p.add_argument("--allow-downgrade", action="store_true", default=False, help="Allow reconcile to downgrade completed stages (use with caution).")
    resume_p.set_defaults(func=cmd_resume)

    rewrite_p = sub.add_parser("rewrite", help="Rewrite script (instruction required) then re-run validation.")
    rewrite_p.add_argument("--channel", required=True)
    rewrite_p.add_argument("--video", required=True)
    rewrite_p.add_argument("--instruction", default="", help="User instruction for rewrite (required unless --instruction-file).")
    rewrite_p.add_argument("--instruction-file", default="", help="Read rewrite instruction from file.")
    rewrite_p.add_argument("--until", default="script_validation", help="Stop when this stage is completed (default: script_validation).")
    rewrite_p.add_argument("--max-iter", type=int, default=30)
    rewrite_p.set_defaults(func=cmd_rewrite)

    args = ap.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

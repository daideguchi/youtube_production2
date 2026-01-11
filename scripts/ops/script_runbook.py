#!/usr/bin/env python3
from __future__ import annotations

"""
script_runbook.py — Script factory ops helper (fixed entrypoint)

This is a thin wrapper around `script_pipeline.runner` that standardizes:
  - new: write a new script from scratch (until script_validation)
  - redo-full: reset and regenerate from scratch (until script_validation)
  - resume: continue from the current pending stage(s) (until script_validation)
  - rewrite: user-instruction rewrite (instruction required; then script_validation)
  - seed-expand: Seed (short A-text) then converge via script_validation (extend/expand)

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

  # Seed→Expand (short seed; then script_validation converges)
  ./scripts/with_ytm_env.sh python3 scripts/ops/script_runbook.py seed-expand --channel CH10 --video 008
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

from factory_common.routing_lockdown import (  # noqa: E402
    assert_no_llm_model_overrides,
    assert_task_overrides_unchanged,
    lockdown_active,
)
from factory_common.paths import logs_root, repo_root, script_pkg_root  # noqa: E402
from factory_common.llm_router import get_router  # noqa: E402
from script_pipeline.runner import ensure_status, reconcile_status, reset_video, run_next, run_stage  # noqa: E402
from script_pipeline.sot import load_status, save_status, status_path  # noqa: E402
from script_pipeline.validator import validate_a_text  # noqa: E402

# Reuse deterministic prompt/context helpers from runner (avoid duplicated logic).
from script_pipeline.runner import (  # noqa: E402
    _a_text_plan_summary_for_prompt,
    _a_text_rules_summary,
    _reduce_paren_marks,
    _reduce_quote_marks,
    _sanitize_a_text_bullet_prefixes,
    _sanitize_a_text_forbidden_statistics,
    _sanitize_a_text_markdown_headings,
    _sanitize_inline_pause_markers,
    _sanitize_quality_gate_context,
    _write_prompt_snapshot,
)


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _truthy(value: Any) -> bool:
    if value is True:
        return True
    if value in (None, "", 0, False):
        return False
    s = str(value).strip().lower()
    return s in {"1", "true", "yes", "on"}


def _apply_llm_overrides_from_args(args: argparse.Namespace) -> None:
    """
    Apply optional LLM router overrides for this process (ops ergonomics).

    Mirrors `packages/script_pipeline/cli.py` so operators can switch models with one flag:
    - --llm-model MODELKEY[,MODELKEY...] (repeatable) → LLM_FORCE_MODELS
    - --llm-slot N → LLM_MODEL_SLOT
    - --exec-slot N → LLM_EXEC_SLOT
    - --llm-task-model TASK=MODELKEY[,MODELKEY...] (repeatable) → LLM_FORCE_TASK_MODELS_JSON
    """
    # Compatibility shim (operator ergonomics):
    # - Some operators reach for `--llm api|think|codex` (used by `./ops`).
    # - Script pipeline is API-only; we accept `--llm api` as a convenience and block others.
    raw_llm = getattr(args, "llm", None)
    if raw_llm is not None and str(raw_llm).strip() != "":
        llm = str(raw_llm).strip().lower()
        if llm != "api":
            raise SystemExit(
                "[POLICY] script pipeline is API-only (no THINK/AGENT/Codex for A-text).\n"
                "- rule: 台本（script_*）は LLM API（Fireworks/DeepSeek）固定。Codex/agent 代行で台本を書かない。\n"
                "- action: rerun with `./ops api script ...` (or omit --llm / use --llm api)."
            )
        # Force API exec-slot unless explicitly overridden via --exec-slot.
        if getattr(args, "exec_slot", None) is None:
            os.environ["LLM_EXEC_SLOT"] = "0"

    if getattr(args, "llm_slot", None) is not None:
        try:
            slot = int(args.llm_slot)
        except Exception:
            raise SystemExit(f"--llm-slot must be an integer; got: {args.llm_slot}")
        if slot < 0:
            raise SystemExit(f"--llm-slot must be >= 0; got: {slot}")
        os.environ["LLM_MODEL_SLOT"] = str(slot)

    if getattr(args, "exec_slot", None) is not None:
        try:
            slot = int(args.exec_slot)
        except Exception:
            raise SystemExit(f"--exec-slot must be an integer; got: {args.exec_slot}")
        if slot < 0:
            raise SystemExit(f"--exec-slot must be >= 0; got: {slot}")
        os.environ["LLM_EXEC_SLOT"] = str(slot)

    if getattr(args, "llm_model", None):
        flattened: list[str] = []
        for raw in args.llm_model or []:
            for part in str(raw).split(","):
                part = part.strip()
                if part:
                    flattened.append(part)
        if flattened:
            if len(flattened) == 1 and flattened[0].isdigit():
                os.environ["LLM_MODEL_SLOT"] = flattened[0]
            else:
                if lockdown_active():
                    raise SystemExit(
                        "Forbidden: --llm-model with non-numeric values under YTM_ROUTING_LOCKDOWN=1. "
                        "Use --llm-slot (numeric) instead, or set YTM_EMERGENCY_OVERRIDE=1 for one-off debugging."
                    )
                os.environ["LLM_FORCE_MODELS"] = ",".join(flattened)

    if getattr(args, "llm_task_model", None):
        mapping: dict[str, list[str]] = {}
        for raw in args.llm_task_model or []:
            spec = str(raw).strip()
            if not spec:
                continue
            if "=" not in spec:
                raise SystemExit(f"--llm-task-model must be TASK=MODELKEY[,MODELKEY...]; got: {spec}")
            task, models = spec.split("=", 1)
            task = task.strip()
            if not task:
                raise SystemExit(f"--llm-task-model task is empty: {spec}")
            model_keys = [m.strip() for m in models.split(",") if m.strip()]
            if not model_keys:
                raise SystemExit(f"--llm-task-model models are empty: {spec}")
            mapping[task] = model_keys
        if mapping:
            if lockdown_active():
                raise SystemExit(
                    "Forbidden: --llm-task-model under YTM_ROUTING_LOCKDOWN=1. "
                    "Use numeric slots (LLM_MODEL_SLOT) and SSOT task routing instead."
                )
            os.environ["LLM_FORCE_TASK_MODELS_JSON"] = json.dumps(mapping, ensure_ascii=False)


def _assert_script_api_only() -> None:
    from factory_common.llm_exec_slots import active_llm_exec_slot_id, effective_llm_mode

    mode = str(effective_llm_mode() or "").strip().lower()
    if mode == "api":
        return
    slot = active_llm_exec_slot_id()
    raise SystemExit(
        "[POLICY] script pipeline is API-only (no THINK/AGENT).\n"
        f"- effective llm_mode: {mode}\n"
        f"- LLM_EXEC_SLOT: {slot.get('id')} (source={slot.get('source')})\n"
        "- action: rerun with `./ops api script ...` (or `--exec-slot 0`)."
    )


def _add_llm_override_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--llm",
        choices=["api", "think", "codex"],
        default=None,
        help="(compat) LLM mode hint. NOTE: script pipeline is API-only; use `api`.",
    )
    p.add_argument(
        "--llm-model",
        action="append",
        help="Force LLM router model key(s) for this run (comma-separated). Can be repeated.",
    )
    p.add_argument(
        "--llm-slot",
        type=int,
        help="Force numeric LLM routing slot (sets LLM_MODEL_SLOT). Preferred over --llm-model.",
    )
    p.add_argument(
        "--exec-slot",
        type=int,
        help="Force numeric LLM exec slot (sets LLM_EXEC_SLOT). Controls api/think/agent/codex exec/failover.",
    )
    p.add_argument(
        "--llm-task-model",
        action="append",
        help="Per-task LLM override: TASK=MODELKEY[,MODELKEY...] (repeatable).",
    )


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


def _should_skip_deleted_episode(st, *, include_deleted: bool) -> tuple[bool, str]:
    """
    Prevent accidental resurrection of episodes that humans deliberately deleted.

    Convention:
    - metadata.deleted_by_human=1 (preferred)
    - metadata.skip_script_regeneration=1 / metadata.skip_script_generation=1
    """
    if include_deleted:
        return False, ""
    meta = getattr(st, "metadata", None) or {}
    if not isinstance(meta, dict):
        return False, ""
    if _truthy(meta.get("deleted_by_human")):
        return True, "deleted_by_human=1"
    if _truthy(meta.get("skip_script_regeneration")) or _truthy(meta.get("skip_script_generation")):
        return True, "skip_script_regeneration=1"
    redo_note = str(meta.get("redo_note") or "").strip().lower()
    if "do not resurrect" in redo_note:
        return True, "redo_note contains 'do not resurrect'"
    return False, ""


def _skipped_item(channel: str, video: str, *, mode: str, note: str) -> ItemResult:
    channel = _norm_channel(channel)
    video = _norm_video(video)
    st = None
    try:
        if status_path(channel, video).exists():
            st = load_status(channel, video)
    except Exception:
        st = None
    status = str(getattr(st, "status", "") or "") if st is not None else ""
    return ItemResult(
        channel=channel,
        video=video,
        mode=mode,
        ok=True,
        status=status,
        script_validation_status=_safe_stage_status(st, "script_validation") if st is not None else "",
        judge_verdict="",
        judge_report_json="",
        semantic_verdict=_safe_semantic_verdict(st) if st is not None else "",
        semantic_alignment_report_json="",
        a_text_path="",
        a_text_local_ok=False,
        a_text_hard_issue_codes=[],
        a_text_stats={},
        a_text_validated_script_hash="",
        a_text_current_script_hash="",
        a_text_hash_match=None,
        planning_coherence=_safe_planning_coherence(st) if st is not None else "",
        status_json=str(status_path(channel, video)),
        note=note,
    )


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


def _maybe_force_revalidate_script_validation_on_input_change(channel: str, video: str) -> bool:
    """
    If `script_validation` is marked completed but the underlying A-text changed,
    flip `script_validation` back to pending so `resume --until script_validation` actually re-runs.

    This prevents "調整したのに検証が走らない"事故（=短い/古い/手直し後の台本が completed のまま）を潰す。
    """
    try:
        st = load_status(channel, video)
    except Exception:
        return False

    sv = st.stages.get("script_validation") if isinstance(st.stages, dict) else None
    if sv is None or getattr(sv, "status", "") != "completed":
        return False

    human_path, mirror_path = _canonical_paths(channel, video)
    in_path = human_path if human_path.exists() else mirror_path
    if not in_path.exists():
        return False

    try:
        from factory_common.alignment import sha1_file
    except Exception:
        sha1_file = None  # type: ignore[assignment]

    if sha1_file is None:
        return False

    try:
        cur_hash = str(sha1_file(in_path))
    except Exception:
        return False

    md = st.metadata if isinstance(st.metadata, dict) else {}

    def _to_int(value: Any) -> Optional[int]:
        if value in (None, ""):
            return None
        try:
            return int(str(value).strip())
        except Exception:
            return None

    cur_target_min = _to_int(md.get("target_chars_min"))
    cur_target_max = _to_int(md.get("target_chars_max"))
    cur_title = str(md.get("sheet_title") or md.get("expected_title") or md.get("title") or "").strip()

    details = getattr(sv, "details", None)
    if not isinstance(details, dict):
        details = {}

    baseline_hash = ""
    try:
        baseline_hash = str(details.get("validated_script_hash") or "").strip()
    except Exception:
        baseline_hash = ""

    baseline_target_min = _to_int(details.get("validated_target_chars_min"))
    baseline_target_max = _to_int(details.get("validated_target_chars_max"))
    baseline_title = str(details.get("validated_expected_title") or "").strip()

    if not baseline_hash:
        try:
            align = st.metadata.get("alignment") if isinstance(st.metadata, dict) else None
            if isinstance(align, dict):
                baseline_hash = str(align.get("script_hash") or "").strip()
        except Exception:
            baseline_hash = ""

    # Legacy episodes may have no baseline hash recorded.
    # In that case, we force a one-time re-validation so "completed" does not silently bypass adjustments.
    if not baseline_hash:
        try:
            sv.status = "pending"
            if isinstance(getattr(sv, "details", None), dict):
                sv.details.setdefault("revalidate", {})["forced_due_to_missing_baseline_hash"] = {
                    "at": _utc_now_compact(),
                    "baseline_script_hash": "",
                    "current_script_hash": cur_hash,
                    "path": str(in_path),
                }
                sv.details["validated_script_hash"] = cur_hash
                sv.details["validated_target_chars_min"] = cur_target_min
                sv.details["validated_target_chars_max"] = cur_target_max
                sv.details["validated_expected_title"] = cur_title
        except Exception:
            pass
        try:
            st.metadata["redo_audio"] = True
            st.status = "script_in_progress"
            save_status(st)
        except Exception:
            return False
        return True

    targets_missing = (baseline_target_min is None and cur_target_min is not None) or (
        baseline_target_max is None and cur_target_max is not None
    )
    targets_changed = (baseline_target_min is not None and cur_target_min is not None and baseline_target_min != cur_target_min) or (
        baseline_target_max is not None and cur_target_max is not None and baseline_target_max != cur_target_max
    )
    title_missing = not baseline_title and bool(cur_title)
    title_changed = bool(baseline_title) and bool(cur_title) and baseline_title != cur_title

    if baseline_hash and baseline_hash == cur_hash and (targets_missing or targets_changed or title_missing or title_changed):
        try:
            sv.status = "pending"
            if isinstance(getattr(sv, "details", None), dict):
                sv.details.setdefault("revalidate", {})["forced_due_to_validation_context_change"] = {
                    "at": _utc_now_compact(),
                    "baseline_script_hash": baseline_hash,
                    "current_script_hash": cur_hash,
                    "baseline_target_chars_min": baseline_target_min,
                    "baseline_target_chars_max": baseline_target_max,
                    "current_target_chars_min": cur_target_min,
                    "current_target_chars_max": cur_target_max,
                    "baseline_expected_title": baseline_title,
                    "current_expected_title": cur_title,
                }
        except Exception:
            pass
        try:
            st.metadata["redo_audio"] = True
            st.status = "script_in_progress"
            save_status(st)
        except Exception:
            return False
        return True

    if baseline_hash and baseline_hash == cur_hash:
        # Bootstrap the per-stage hash for older episodes.
        try:
            if isinstance(getattr(sv, "details", None), dict):
                if not sv.details.get("validated_script_hash"):
                    sv.details["validated_script_hash"] = cur_hash
                if "validated_target_chars_min" not in sv.details:
                    sv.details["validated_target_chars_min"] = cur_target_min
                if "validated_target_chars_max" not in sv.details:
                    sv.details["validated_target_chars_max"] = cur_target_max
                if "validated_expected_title" not in sv.details:
                    sv.details["validated_expected_title"] = cur_title
                save_status(st)
        except Exception:
            pass
        return False

    try:
        sv.status = "pending"
        if isinstance(getattr(sv, "details", None), dict):
            sv.details.setdefault("revalidate", {})["forced_due_to_input_change"] = {
                "at": _utc_now_compact(),
                "baseline_script_hash": baseline_hash,
                "current_script_hash": cur_hash,
                "path": str(in_path),
            }
            sv.details["validated_target_chars_min"] = cur_target_min
            sv.details["validated_target_chars_max"] = cur_target_max
            sv.details["validated_expected_title"] = cur_title
        st.metadata["redo_audio"] = True
        st.status = "script_in_progress"
        save_status(st)
        return True
    except Exception:
        return False


def _stamp_script_validation_hash(channel: str, video: str) -> None:
    """
    Record the canonical script hash at the time `script_validation` is completed.
    This is used by `resume` to detect manual A-text edits and force re-validation.
    """
    try:
        st = load_status(channel, video)
    except Exception:
        return
    sv = st.stages.get("script_validation") if isinstance(st.stages, dict) else None
    if sv is None or getattr(sv, "status", "") != "completed":
        return
    human_path, mirror_path = _canonical_paths(channel, video)
    in_path = human_path if human_path.exists() else mirror_path
    if not in_path.exists():
        return
    try:
        from factory_common.alignment import sha1_file
    except Exception:
        return
    try:
        cur_hash = str(sha1_file(in_path))
    except Exception:
        return
    try:
        if isinstance(getattr(sv, "details", None), dict):
            sv.details["validated_script_hash"] = cur_hash
            sv.details["validated_script_hash_at"] = _utc_now_compact()
            md = st.metadata if isinstance(st.metadata, dict) else {}
            sv.details["validated_target_chars_min"] = md.get("target_chars_min")
            sv.details["validated_target_chars_max"] = md.get("target_chars_max")
            sv.details["validated_expected_title"] = str(
                md.get("sheet_title") or md.get("expected_title") or md.get("title") or ""
            ).strip()
        save_status(st)
    except Exception:
        return


@dataclass(frozen=True)
class ItemResult:
    channel: str
    video: str
    mode: str
    ok: bool
    status: str
    script_validation_status: str
    judge_verdict: str
    judge_report_json: str
    semantic_verdict: str
    semantic_alignment_report_json: str
    a_text_path: str
    a_text_local_ok: bool
    a_text_hard_issue_codes: List[str]
    a_text_stats: Dict[str, Any]
    a_text_validated_script_hash: str
    a_text_current_script_hash: str
    a_text_hash_match: Optional[bool]
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
            "judge_verdict": self.judge_verdict,
            "judge_report_json": self.judge_report_json,
            "semantic_verdict": self.semantic_verdict,
            "semantic_alignment_report_json": self.semantic_alignment_report_json,
            "a_text_path": self.a_text_path,
            "a_text_local_ok": self.a_text_local_ok,
            "a_text_hard_issue_codes": list(self.a_text_hard_issue_codes or []),
            "a_text_stats": dict(self.a_text_stats or {}),
            "a_text_validated_script_hash": self.a_text_validated_script_hash,
            "a_text_current_script_hash": self.a_text_current_script_hash,
            "a_text_hash_match": self.a_text_hash_match,
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


def _safe_llm_qc_reports(st) -> tuple[str, str, str]:
    """
    Surface existing LLM QC artifacts (no extra generation):
    - content/analysis/quality_gate/judge_latest.json
    - content/analysis/alignment/semantic_alignment.json
    Returns (judge_verdict, judge_report_abs, semantic_alignment_report_abs).
    """
    sv = st.stages.get("script_validation") if isinstance(getattr(st, "stages", None), dict) else None
    if sv is None:
        return "", "", ""
    details = getattr(sv, "details", None)
    if not isinstance(details, dict):
        return "", "", ""

    base = status_path(str(st.channel), str(st.video)).parent

    judge_verdict = ""
    judge_report_abs = ""
    llm_gate = details.get("llm_quality_gate")
    if isinstance(llm_gate, dict):
        judge_verdict = str(llm_gate.get("verdict") or "").strip()
        rel = str(llm_gate.get("judge_report") or "").strip()
        if rel:
            judge_report_abs = str((base / rel).resolve())

    sem_report_abs = ""
    sem_gate = details.get("semantic_alignment_gate")
    if isinstance(sem_gate, dict):
        rel = str(sem_gate.get("report_path") or "").strip()
        if rel:
            sem_report_abs = str((base / rel).resolve())

    return judge_verdict, judge_report_abs, sem_report_abs


def _seed_template_path() -> Path:
    return script_pkg_root() / "prompts" / "a_text_seed_prompt.txt"


def _load_sources_doc() -> Dict[str, Any]:
    """
    Read `configs/sources.yaml` once (runbook lifetime) and return the parsed dict.

    Note:
    - `script_pipeline.runner.ensure_status()` intentionally avoids overriding existing
      per-episode targets in `status.json` (backfill-only). For ops, we want the
      *channel SSOT* (`configs/sources.yaml`) to win when it changes, otherwise
      validation keeps using stale numbers and costs explode.
    """
    import yaml  # local import to keep CLI startup minimal

    path = repo_root() / "configs" / "sources.yaml"
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _refresh_episode_targets_from_sources(st) -> bool:
    """
    Ensure `status.json: metadata.target_chars_*` and `target_word_count` follow the latest
    channel SSOT (`configs/sources.yaml`).

    This does NOT rewrite any A-text. It only updates validation/word-budget targets.
    """
    if _truthy((st.metadata or {}).get("targets_locked")):
        return False

    def _to_int(value: Any) -> Optional[int]:
        if value in (None, ""):
            return None
        try:
            return int(str(value).strip())
        except Exception:
            return None

    doc = _load_sources_doc()
    channels = doc.get("channels") if isinstance(doc, dict) else None
    ch_cfg = channels.get(str(st.channel).upper()) if isinstance(channels, dict) else None
    if not isinstance(ch_cfg, dict):
        return False

    cfg_min = _to_int(ch_cfg.get("target_chars_min"))
    cfg_max = _to_int(ch_cfg.get("target_chars_max"))
    cur_min = _to_int((st.metadata or {}).get("target_chars_min"))
    cur_max = _to_int((st.metadata or {}).get("target_chars_max"))

    changed = False
    if cfg_min is not None and cfg_min != cur_min:
        st.metadata["target_chars_min"] = cfg_min
        changed = True
    if cfg_max is not None and cfg_max != cur_max:
        st.metadata["target_chars_max"] = cfg_max
        changed = True

    # Keep WORD_TARGET derivation in sync; otherwise chapter-draft stages can keep using stale values.
    if changed:
        twc: Optional[int] = None
        tmin = _to_int((st.metadata or {}).get("target_chars_min"))
        tmax = _to_int((st.metadata or {}).get("target_chars_max"))
        if isinstance(tmin, int) and isinstance(tmax, int) and tmax >= tmin:
            twc = int(round(tmin + (tmax - tmin) * 0.6))
        elif isinstance(tmin, int):
            twc = tmin
        elif isinstance(tmax, int):
            twc = tmax
        if isinstance(twc, int) and twc > 0:
            st.metadata["target_word_count"] = twc

        # If validation was already completed under old targets, force re-validation.
        try:
            sv = st.stages.get("script_validation")
        except Exception:
            sv = None
        if sv is not None and getattr(sv, "status", "") == "completed":
            sv.status = "pending"
            if isinstance(getattr(sv, "details", None), dict):
                sv.details["revalidated_due_to_target_change"] = True

        st.metadata["targets_synced_at"] = _utc_now_compact()
        st.metadata["targets_synced_from"] = "configs/sources.yaml"

    return changed


def _seed_targets(meta: Dict[str, Any]) -> Dict[str, int]:
    """
    Decide a short-but-structured seed length target.
    Goal: keep seed cost bounded while still covering all sections.
    """
    try:
        target_min = int(str(meta.get("target_chars_min") or "").strip())
    except Exception:
        target_min = 0
    if target_min <= 0:
        target_min = 6000

    aim = int(round(target_min * 0.42))
    aim = max(2500, min(9000, aim))
    seed_min = max(1200, int(round(aim * 0.85)))
    seed_max = max(seed_min + 200, int(round(aim * 1.15)))
    # Hard guard: never ask the seed to exceed the final minimum; this mode exists to keep the first pass cheap.
    seed_max = min(seed_max, max(1500, target_min - 200))
    aim = min(aim, max(1200, seed_max - 100))
    return {"aim": aim, "min": seed_min, "max": seed_max}


def _render_seed_prompt(st, *, seed_target: Dict[str, int]) -> str:
    tpl_path = _seed_template_path()
    if not tpl_path.exists():
        raise SystemExit(f"seed template missing: {tpl_path}")

    title = str(st.metadata.get("sheet_title") or st.metadata.get("expected_title") or st.metadata.get("title") or st.script_id).strip()
    try:
        tmin = int(str(st.metadata.get("target_chars_min") or "").strip())
    except Exception:
        tmin = 0
    try:
        tmax = int(str(st.metadata.get("target_chars_max") or "").strip())
    except Exception:
        tmax = 0

    plan_summary = _a_text_plan_summary_for_prompt(st, max_chars=1100)
    persona = _sanitize_quality_gate_context(str(st.metadata.get("persona") or ""), max_chars=900)
    channel_prompt = _sanitize_quality_gate_context(str(st.metadata.get("a_text_channel_prompt") or st.metadata.get("script_prompt") or ""), max_chars=900)
    benchmarks = _sanitize_quality_gate_context(str(st.metadata.get("a_text_benchmark_excerpts") or ""), max_chars=650)
    rules = _a_text_rules_summary(st.metadata or {})

    placeholders = {
        "CHANNEL_CODE": str(st.channel),
        "VIDEO_ID": f"{st.channel}-{st.video}",
        "TITLE": title,
        "TARGET_CHARS_MIN": str(tmin or ""),
        "TARGET_CHARS_MAX": str(tmax or ""),
        "SEED_TARGET_CHARS": str(int(seed_target["aim"])),
        "SEED_TARGET_MIN": str(int(seed_target["min"])),
        "SEED_TARGET_MAX": str(int(seed_target["max"])),
        "A_TEXT_PLAN_SUMMARY": plan_summary,
        "PERSONA": persona,
        "CHANNEL_PROMPT": channel_prompt,
        "BENCHMARK_EXCERPTS": benchmarks,
        "A_TEXT_RULES_SUMMARY": rules,
    }

    tpl = tpl_path.read_text(encoding="utf-8")
    for k, v in placeholders.items():
        tpl = tpl.replace(f"<<{k}>>", str(v or "").strip())
    return tpl.strip() + "\n"


def _sanitize_seed_text(text: str, *, meta: Dict[str, Any]) -> str:
    cleaned = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    cleaned = _sanitize_a_text_markdown_headings(cleaned)
    cleaned = _sanitize_a_text_bullet_prefixes(cleaned)
    cleaned = _sanitize_a_text_forbidden_statistics(cleaned)
    cleaned = _sanitize_inline_pause_markers(cleaned)
    # Keep TTS/readability guardrails aligned with global caps.
    try:
        quote_max = int((meta or {}).get("a_text_quote_marks_max") or 20)
    except Exception:
        quote_max = 20
    try:
        paren_max = int((meta or {}).get("a_text_paren_marks_max") or 10)
    except Exception:
        paren_max = 10
    if quote_max > 0:
        cleaned = _reduce_quote_marks(cleaned, quote_max)
    if paren_max > 0:
        cleaned = _reduce_paren_marks(cleaned, paren_max)
    # Remove meta/citation/URL leakage deterministically.
    try:
        from factory_common.text_sanitizer import strip_meta_from_script

        sanitized = strip_meta_from_script(cleaned)
        if sanitized.text.strip():
            cleaned = sanitized.text.strip()
    except Exception:
        pass
    return cleaned.strip() + "\n"


def cmd_seed_expand(args: argparse.Namespace) -> int:
    ch = _norm_channel(args.channel)
    no = _norm_video(args.video)
    force_seed = bool(getattr(args, "force_seed", False))

    # Ensure status + metadata are present (targets/persona/prompt).
    st = ensure_status(ch, no, title=None)
    if _refresh_episode_targets_from_sources(st):
        save_status(st)
        st = load_status(ch, no)
    human_path, mirror_path = _canonical_paths(ch, no)
    seed_exists = (human_path.exists() and human_path.stat().st_size > 0) or (mirror_path.exists() and mirror_path.stat().st_size > 0)

    seed_note = ""
    if seed_exists and not force_seed:
        seed_note = "seed_exists"
    else:
        # Generate a short seed (one-shot; no retry-on-length).
        seed_task = "script_a_text_seed"
        seed_routing_key = f"{ch}-{no}_seed"

        seed_target = _seed_targets(st.metadata or {})
        prompt = _render_seed_prompt(st, seed_target=seed_target)
        try:
            base = status_path(ch, no).parent
            _write_prompt_snapshot(
                base / "content" / "analysis" / "prompt_snapshots",
                "seed_expand_prompt.txt",
                prompt,
                base=base,
            )
        except Exception:
            pass

        router = get_router()
        prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
        prev_retry_on_length = os.environ.get("LLM_RETRY_ON_LENGTH")
        os.environ["LLM_ROUTING_KEY"] = seed_routing_key
        os.environ["LLM_RETRY_ON_LENGTH"] = "0"
        try:
            result = router.call_with_raw(
                task=seed_task,
                messages=[{"role": "user", "content": prompt}],
            )
        finally:
            if prev_routing_key is None:
                os.environ.pop("LLM_ROUTING_KEY", None)
            else:
                os.environ["LLM_ROUTING_KEY"] = prev_routing_key
            if prev_retry_on_length is None:
                os.environ.pop("LLM_RETRY_ON_LENGTH", None)
            else:
                os.environ["LLM_RETRY_ON_LENGTH"] = prev_retry_on_length

        seed_raw = _extract_llm_text_content(result)
        seed_text = _sanitize_seed_text(seed_raw, meta=st.metadata or {})

        # Minimal safety check: allow non-length issues to be fixed by script_validation,
        # but never write obviously dangerous content (URLs, citations, placeholders, etc.).
        issues, _stats = validate_a_text(
            seed_text,
            {**(st.metadata or {}), "target_chars_min": 0, "target_chars_max": ""},
        )
        hard = [it for it in issues if str((it or {}).get("severity") or "error").lower() != "warning"]
        hard_codes = {str(it.get("code")) for it in hard if isinstance(it, dict) and it.get("code")}
        fatal = {
            "empty_script",
            "dummy_a_text",
            "replacement_character",
            "forbidden_unicode_control",
            "forbidden_url",
            "forbidden_citation",
            "template_token",
            "placeholder_token",
        }
        fatal_hit = sorted(hard_codes & fatal)
        if fatal_hit:
            raise SystemExit(f"seed_invalid_fatal ({fatal_hit})")

        # Backup existing (if any), then write canonical + mirror.
        backup_dir = mirror_path.parent / "analysis" / "seed"
        backup_dir.mkdir(parents=True, exist_ok=True)
        if seed_exists:
            src = human_path if human_path.exists() else mirror_path
            try:
                backup_path = backup_dir / f"backup_{_utc_now_compact()}_{src.name}"
                backup_path.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            except Exception:
                backup_path = None
        else:
            backup_path = None

        mirror_path.parent.mkdir(parents=True, exist_ok=True)
        mirror_path.write_text(seed_text, encoding="utf-8")
        human_path.write_text(seed_text, encoding="utf-8")

        # Record seed metadata for auditability/cost tracking.
        st = load_status(ch, no)
        st.metadata["seed"] = {
            "mode": "seed-expand",
            "generated_at": _utc_now_compact(),
            "task": seed_task,
            "routing_key": seed_routing_key,
            "seed_target": seed_target,
            "backup_path": str(backup_path) if backup_path else "",
            "provider": str(result.get("provider") or ""),
            "model": str(result.get("model") or ""),
            "usage": result.get("usage") or {},
            "request_id": str(result.get("request_id") or ""),
        }
        # Force downstream validation to re-run on the new text.
        if "script_validation" in st.stages:
            st.stages["script_validation"].status = "pending"
        st.status = "script_in_progress"
        save_status(st)
        seed_note = "seed_generated"

    # Converge via script_validation (includes extend/expand).
    # Ensure Planning↔Script alignment stamp exists so validation can run (deterministic, no LLM).
    reconcile_status(ch, no, allow_downgrade=False)
    run_stage(ch, no, "script_validation", title=None)
    _stamp_script_validation_hash(ch, no)

    res = _result_for(ch, no, mode="seed-expand", note=seed_note)
    print(json.dumps(res.as_dict(), ensure_ascii=False, indent=2))
    return 0 if res.ok else 2


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
    judge_verdict, judge_report, sem_report = _safe_llm_qc_reports(st)
    semantic_verdict = _safe_semantic_verdict(st)
    # Deterministic local QC (no LLM):
    # - validate the *current* canonical A-text against global rules
    # - detect stale "completed" status when humans/agents edited the file after validation
    a_text_path = ""
    a_text_local_ok = False
    a_text_hard_issue_codes: List[str] = []
    a_text_stats: Dict[str, Any] = {}
    a_text_validated_script_hash = ""
    a_text_current_script_hash = ""
    a_text_hash_match: Optional[bool] = None

    try:
        from factory_common.alignment import sha1_file
    except Exception:
        sha1_file = None  # type: ignore[assignment]

    try:
        from script_pipeline.validator import validate_a_text
    except Exception:
        validate_a_text = None  # type: ignore[assignment]

    try:
        human_path, mirror_path = _canonical_paths(channel, video)
        in_path = human_path if human_path.exists() else mirror_path
        if in_path.exists():
            a_text_path = str(in_path)
            if sha1_file is not None:
                try:
                    a_text_current_script_hash = str(sha1_file(in_path))
                except Exception:
                    a_text_current_script_hash = ""

            if validate_a_text is not None:
                try:
                    text = in_path.read_text(encoding="utf-8")
                    issues, stats = validate_a_text(text, st.metadata or {})
                    a_text_stats = stats if isinstance(stats, dict) else {}
                    hard = [
                        it
                        for it in (issues or [])
                        if isinstance(it, dict) and str(it.get("severity") or "error").lower() != "warning"
                    ]
                    a_text_hard_issue_codes = sorted(
                        {str(it.get("code")) for it in hard if it.get("code")}
                    )[:12]
                    a_text_local_ok = len(hard) == 0
                except Exception:
                    pass
    except Exception:
        pass

    try:
        sv = st.stages.get("script_validation") if isinstance(getattr(st, "stages", None), dict) else None
        if sv is not None and isinstance(getattr(sv, "details", None), dict):
            a_text_validated_script_hash = str(sv.details.get("validated_script_hash") or "").strip()
    except Exception:
        a_text_validated_script_hash = ""

    if a_text_validated_script_hash and a_text_current_script_hash:
        a_text_hash_match = a_text_validated_script_hash == a_text_current_script_hash

    script_validation_completed = _safe_stage_status(st, "script_validation") == "completed"
    fresh_enough = (a_text_hash_match is not False)  # True/None are OK; False means stale
    ok = bool(script_validation_completed) and (semantic_verdict != "major") and bool(a_text_local_ok) and bool(fresh_enough)
    return ItemResult(
        channel=channel,
        video=video,
        mode=mode,
        ok=ok,
        status=str(st.status or ""),
        script_validation_status=_safe_stage_status(st, "script_validation"),
        judge_verdict=judge_verdict,
        judge_report_json=judge_report,
        semantic_verdict=semantic_verdict,
        semantic_alignment_report_json=sem_report,
        a_text_path=a_text_path,
        a_text_local_ok=bool(a_text_local_ok),
        a_text_hard_issue_codes=list(a_text_hard_issue_codes or []),
        a_text_stats=dict(a_text_stats or {}),
        a_text_validated_script_hash=a_text_validated_script_hash,
        a_text_current_script_hash=a_text_current_script_hash,
        a_text_hash_match=a_text_hash_match,
        planning_coherence=_safe_planning_coherence(st),
        status_json=str(status_path(channel, video)),
        note=note,
    )


def cmd_new(args: argparse.Namespace) -> int:
    ch = _norm_channel(args.channel)
    no = _norm_video(args.video)
    max_iter = int(args.max_iter)
    until = str(args.until)

    # Ensure that a pre-existing status (if any) follows the latest channel targets.
    if status_path(ch, no).exists():
        st = load_status(ch, no)
        if _refresh_episode_targets_from_sources(st):
            save_status(st)
        if until in {"script_validation", "audio_synthesis"}:
            _maybe_force_revalidate_script_validation_on_input_change(ch, no)

    _run_until(ch, no, until_stage=until, max_iter=max_iter)
    _stamp_script_validation_hash(ch, no)
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
            st_cached = load_status(ch, no) if status_path(ch, no).exists() else None
            if st_cached is not None:
                skip, why = _should_skip_deleted_episode(st_cached, include_deleted=bool(getattr(args, "include_deleted", False)))
                if skip:
                    items.append(_skipped_item(ch, no, mode=f"redo:{mode}", note=f"skipped ({why})"))
                    continue

            if mode == "validate":
                # Validation should follow the latest channel targets even for existing status.json.
                if st_cached is not None:
                    if _refresh_episode_targets_from_sources(st_cached):
                        save_status(st_cached)
                run_stage(ch, no, "script_validation", title=None)
                _stamp_script_validation_hash(ch, no)
                note = "validated"
            elif mode == "continue":
                # Resume without reset: reconcile + run pending stages until `until`.
                # If status.json doesn't exist yet, fall back to regenerate for that episode.
                if st_cached is not None:
                    if _refresh_episode_targets_from_sources(st_cached):
                        save_status(st_cached)
                    reconcile_status(ch, no, allow_downgrade=False)
                    if until in {"script_validation", "audio_synthesis"}:
                        _maybe_force_revalidate_script_validation_on_input_change(ch, no)
                    _run_until(ch, no, until_stage=until, max_iter=max_iter)
                    _stamp_script_validation_hash(ch, no)
                    note = "continued"
                else:
                    reset_video(ch, no, wipe_research=bool(args.wipe_research))
                    _run_until(ch, no, until_stage=until, max_iter=max_iter)
                    _stamp_script_validation_hash(ch, no)
                    note = "reset+continued"
            elif mode == "regenerate":
                reset_video(ch, no, wipe_research=bool(args.wipe_research))
                _run_until(ch, no, until_stage=until, max_iter=max_iter)
                _stamp_script_validation_hash(ch, no)
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
                    judge_verdict="",
                    judge_report_json="",
                    semantic_verdict="",
                    semantic_alignment_report_json="",
                    a_text_path="",
                    a_text_local_ok=False,
                    a_text_hard_issue_codes=[],
                    a_text_stats={},
                    a_text_validated_script_hash="",
                    a_text_current_script_hash="",
                    a_text_hash_match=None,
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

    st = load_status(ch, no)
    if _refresh_episode_targets_from_sources(st):
        save_status(st)

    if bool(args.reconcile):
        reconcile_status(ch, no, allow_downgrade=bool(args.allow_downgrade))

    # If operators manually adjusted the A-text, ensure `script_validation` is actually re-executed on resume.
    if until in {"script_validation", "audio_synthesis"}:
        _maybe_force_revalidate_script_validation_on_input_change(ch, no)

    if bool(getattr(args, "force_script_validation", False)) and until in {"script_validation", "audio_synthesis"}:
        try:
            st = load_status(ch, no)
            sv = st.stages.get("script_validation") if isinstance(getattr(st, "stages", None), dict) else None
            if sv is not None:
                sv.status = "pending"
                if isinstance(getattr(sv, "details", None), dict):
                    sv.details.setdefault("revalidate", {})["forced_by_operator"] = {"at": _utc_now_compact()}
                st.status = "script_in_progress"
                save_status(st)
        except Exception:
            pass

    # Safety: resuming to `script_validation` should not implicitly regenerate earlier stages
    # (those stages may be intentionally purged while A-text already exists).
    st = load_status(ch, no)
    if _safe_stage_status(st, until) != "completed":
        if until in {"script_validation", "audio_synthesis"}:
            human_path, mirror_path = _canonical_paths(ch, no)
            if not (human_path.exists() or mirror_path.exists()):
                # No SoT A-text to validate yet.
                # This commonly happens after artifacts are purged/archived (e.g., external offload).
                # In this case, we *must* allow status downgrade so `run_next()` can rebuild
                # the missing durable artifacts instead of getting stuck on `script_validation`.
                reconcile_status(ch, no, allow_downgrade=True)
                _run_until(ch, no, until_stage=until, max_iter=max_iter)
            else:
                run_stage(ch, no, until, title=None)
        else:
            _run_until(ch, no, until_stage=until, max_iter=max_iter)

    _stamp_script_validation_hash(ch, no)
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
    if _refresh_episode_targets_from_sources(st):
        save_status(st)
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
        codes: List[str] = []
        for it in hard:
            if not isinstance(it, dict):
                continue
            c = str(it.get("code") or "").strip()
            if c and c not in codes:
                codes.append(c)
        hint = f" codes={','.join(codes[:8])}" if codes else ""
        raise SystemExit(f"rewrite_failed: deterministic_validation_failed ({len(hard)} issues){hint}")

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
    _stamp_script_validation_hash(ch, no)

    res = _result_for(ch, no, mode="rewrite", note=f"backup={backup_path}")
    print(json.dumps(res.as_dict(), ensure_ascii=False, indent=2))
    return 0 if res.ok else 2


def main() -> int:
    ap = argparse.ArgumentParser(description="Script pipeline runbook helper (new/redo).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    new_p = sub.add_parser("new", help="Create a new script by running pending stages until stable.")
    _add_llm_override_flags(new_p)
    new_p.add_argument("--channel", required=True)
    new_p.add_argument("--video", required=True)
    new_p.add_argument("--until", default="script_validation", help="Stop when this stage is completed (default: script_validation).")
    new_p.add_argument("--max-iter", type=int, default=30)
    new_p.set_defaults(func=cmd_new)

    redo_p = sub.add_parser("redo", help="Redo existing scripts for a range (validate, continue, or regenerate).")
    _add_llm_override_flags(redo_p)
    redo_p.add_argument("--channel", required=True)
    redo_p.add_argument("--from", dest="from_video", required=True)
    redo_p.add_argument("--to", dest="to_video", required=True)
    redo_p.add_argument("--mode", choices=["validate", "continue", "regenerate"], default="validate")
    redo_p.add_argument("--wipe-research", action="store_true", help="When regenerating, also wipe research outputs.")
    redo_p.add_argument(
        "--include-deleted",
        action="store_true",
        help="Also run on episodes marked deleted_by_human/skip_script_regeneration (default: skip to prevent resurrection).",
    )
    redo_p.add_argument(
        "--until",
        default="script_validation",
        help="(continue/regenerate) stop when this stage is completed (default: script_validation).",
    )
    redo_p.add_argument("--max-iter", type=int, default=30, help="Max stage executions per video (continue/regenerate mode).")
    redo_p.set_defaults(func=cmd_redo)

    redo_full_p = sub.add_parser("redo-full", help="Reset + regenerate from scratch for a range (alias of redo --mode regenerate).")
    _add_llm_override_flags(redo_full_p)
    redo_full_p.add_argument("--channel", required=True)
    redo_full_p.add_argument("--from", dest="from_video", required=True)
    redo_full_p.add_argument("--to", dest="to_video", required=True)
    redo_full_p.add_argument("--wipe-research", action="store_true", help="Also wipe research outputs.")
    redo_full_p.add_argument(
        "--include-deleted",
        action="store_true",
        help="Also run on episodes marked deleted_by_human/skip_script_regeneration (default: skip to prevent resurrection).",
    )
    redo_full_p.add_argument("--until", default="script_validation", help="Stop when this stage is completed (default: script_validation).")
    redo_full_p.add_argument("--max-iter", type=int, default=30, help="Max stage executions per video.")
    redo_full_p.set_defaults(func=cmd_redo_full)

    resume_p = sub.add_parser(
        "resume",
        help="Adjust/resume a single episode (sync targets + reconcile + re-run validation when needed).",
    )
    _add_llm_override_flags(resume_p)
    resume_p.add_argument("--channel", required=True)
    resume_p.add_argument("--video", required=True)
    resume_p.add_argument("--until", default="script_validation", help="Stop when this stage is completed (default: script_validation).")
    resume_p.add_argument("--max-iter", type=int, default=30)
    resume_p.add_argument(
        "--force-script-validation",
        action="store_true",
        default=False,
        help="Force `script_validation` to re-run even if it's already completed (use when prompts/QC changed and you want revalidation without touching the script file).",
    )
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
    _add_llm_override_flags(rewrite_p)
    rewrite_p.add_argument("--channel", required=True)
    rewrite_p.add_argument("--video", required=True)
    rewrite_p.add_argument("--instruction", default="", help="User instruction for rewrite (required unless --instruction-file).")
    rewrite_p.add_argument("--instruction-file", default="", help="Read rewrite instruction from file.")
    rewrite_p.add_argument("--until", default="script_validation", help="Stop when this stage is completed (default: script_validation).")
    rewrite_p.add_argument("--max-iter", type=int, default=30)
    rewrite_p.set_defaults(func=cmd_rewrite)

    seed_p = sub.add_parser("seed-expand", help="Seed one-shot then converge via script_validation (extend/expand).")
    _add_llm_override_flags(seed_p)
    seed_p.add_argument("--channel", required=True)
    seed_p.add_argument("--video", required=True)
    seed_p.add_argument("--force-seed", action="store_true", help="Overwrite existing seed A-text by regenerating it (extra LLM call).")
    seed_p.set_defaults(func=cmd_seed_expand)

    args = ap.parse_args()
    assert_no_llm_model_overrides(context="script_runbook.py (startup)")
    assert_task_overrides_unchanged(context="script_runbook.py (startup)")
    _apply_llm_overrides_from_args(args)
    _assert_script_api_only()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

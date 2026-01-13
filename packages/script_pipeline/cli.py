#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from pprint import pprint

from .runner import run_stage, run_next
from .sot import init_status, load_status, status_path
from .runner import _load_stage_defs  # internal use for init ordering
from .runner import _load_sources  # internal use for SSOT syncing
from .sot import save_status
from .sot import DATA_ROOT
from factory_common.routing_lockdown import (
    assert_no_llm_model_overrides,
    assert_task_overrides_unchanged,
    lockdown_active,
)


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _truthy(value: object) -> bool:
    if value is True:
        return True
    if value in (None, "", 0, False):
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value).strip())
    except Exception:
        return None


def _sync_episode_targets_from_sources(ch: str, no: str) -> bool:
    """
    Keep per-episode `status.json` length targets in sync with channel SSOT (`configs/sources.yaml`).

    Why:
    - `runner.ensure_status()` is intentionally backfill-only for existing status.json.
    - Operators/agents sometimes run this CLI directly (not via script_runbook.py),
      which can lead to stale `target_chars_*` and expensive "length rescue" loops.

    This is metadata-only and never rewrites A-text.
    To pin manual per-episode overrides, set `metadata.targets_locked=1`.
    """
    if not status_path(ch, no).exists():
        return False

    st = load_status(ch, no)
    meta = st.metadata or {}
    if _truthy(meta.get("targets_locked")):
        return False

    cfg = _load_sources(ch) or {}
    cfg_min = _parse_int(cfg.get("target_chars_min"))
    cfg_max = _parse_int(cfg.get("target_chars_max"))
    cur_min = _parse_int(meta.get("target_chars_min"))
    cur_max = _parse_int(meta.get("target_chars_max"))

    changed = False
    if isinstance(cfg_min, int) and cfg_min != cur_min:
        meta["target_chars_min"] = cfg_min
        changed = True
    if isinstance(cfg_max, int) and cfg_max != cur_max:
        meta["target_chars_max"] = cfg_max
        changed = True

    if changed:
        # Keep WORD_TARGET derivation aligned with runner._total_word_target biasing.
        tmin = _parse_int(meta.get("target_chars_min"))
        tmax = _parse_int(meta.get("target_chars_max"))
        twc: int | None = None
        if isinstance(tmin, int) and isinstance(tmax, int) and tmax >= tmin:
            twc = int(round(tmin + (tmax - tmin) * 0.6))
        elif isinstance(tmin, int):
            twc = tmin
        elif isinstance(tmax, int):
            twc = tmax
        if isinstance(twc, int) and twc > 0:
            meta["target_word_count"] = twc

        # If validation was already completed under old targets, force re-validation.
        sv = st.stages.get("script_validation") if isinstance(st.stages, dict) else None
        if sv is not None and getattr(sv, "status", "") == "completed":
            sv.status = "pending"
            if isinstance(getattr(sv, "details", None), dict):
                sv.details["revalidated_due_to_target_change"] = True

        meta["targets_synced_at"] = _utc_now_compact()
        meta["targets_synced_from"] = "configs/sources.yaml"
        st.metadata = meta
        save_status(st)

    return changed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="New script pipeline runner (isolated).")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--channel", required=True, help="Channel code, e.g., CH06")
    common.add_argument("--video", required=True, help="Video number, e.g., 033")
    common.add_argument("--title", help="Title (required when initializing a missing status)")
    common.add_argument(
        "--llm-model",
        action="append",
        help="Force LLM router model key(s) for this run (comma-separated). DEBUG ONLY; prefer --llm-slot.",
    )
    common.add_argument(
        "--llm-slot",
        type=int,
        help="Force numeric LLM routing slot (sets LLM_MODEL_SLOT). Preferred over --llm-model.",
    )
    common.add_argument(
        "--exec-slot",
        type=int,
        help="Force numeric LLM exec slot (sets LLM_EXEC_SLOT). Controls api/think/agent/codex exec/failover.",
    )
    common.add_argument(
        "--llm-task-model",
        action="append",
        help="Per-task LLM override: TASK=MODELKEY[,MODELKEY...] (repeatable). DEBUG ONLY.",
    )
    common.add_argument(
        "--llm-task-temperature",
        action="append",
        help="Per-task temperature override: TASK=FLOAT (repeatable). DEBUG ONLY.",
    )
    common.add_argument(
        "--llm-task-option",
        action="append",
        help="Per-task option override: TASK=JSON_OBJECT (repeatable). DEBUG ONLY.",
    )

    sub.add_parser("init", parents=[common], help="Initialize status.json if missing")
    run_p = sub.add_parser("run", parents=[common], help="Run specific stage")
    run_p.add_argument("--stage", required=True, help="Stage name to run")
    sub.add_parser("next", parents=[common], help="Run the first pending stage")
    run_all_p = sub.add_parser("run-all", parents=[common], help="Run pending stages until completion or limit")
    run_all_p.add_argument("--max-iter", type=int, default=30, help="Maximum stage executions (default: 30)")
    sub.add_parser("status", parents=[common], help="Show status.json")
    sub.add_parser("validate", parents=[common], help="Validate required outputs")
    reconcile_p = sub.add_parser("reconcile", parents=[common], help="Reconcile status based on existing outputs")
    reconcile_p.add_argument(
        "--allow-downgrade",
        action="store_true",
        help="Allow downgrading completed stages when required artifacts are missing/invalid",
    )
    reset_p = sub.add_parser("reset", parents=[common], help="Reset outputs/status for a video")
    reset_p.add_argument("--wipe-research", action="store_true", help="Also delete research outputs")

    rebuild_p = sub.add_parser(
        "a-text-rebuild",
        parents=[common],
        help="Rebuild A-text from SSOT patterns (plan->draft), then optionally validate",
    )
    rebuild_p.add_argument(
        "--no-validate",
        action="store_true",
        help="Do not run script_validation after rebuilding",
    )
    rebuild_p.add_argument(
        "--reason",
        default="manual_rebuild",
        help="Reason tag recorded in status/analysis (default: manual_rebuild)",
    )
    
    # Audio command wrapper
    audio_p = sub.add_parser("audio", parents=[common], help="Run audio synthesis")
    audio_p.add_argument("--resume", action="store_true", help="Resume from existing chunks")
    audio_p.add_argument(
        "--indices",
        default="",
        help="Comma-separated segment indices to regenerate (0-based). Example: '3,10'",
    )
    audio_p.add_argument(
        "--prepass",
        action="store_true",
        help="Reading-only pass (no wav synthesis). Generates/updates audio log for inspection.",
    )
    audio_p.add_argument(
        "--allow-unvalidated",
        action="store_true",
        help="Allow TTS even when script_validation is not completed (not recommended).",
    )
    audio_p.add_argument(
        "--force-overwrite-final",
        action="store_true",
        help="Force overwrite final artifacts (also disables AUTO finalize_existing when manual wav/srt exists).",
    )

    align_p = sub.add_parser("semantic-align", parents=[common], help="Check/fix semantic alignment (title/thumbnail ↔ script)")
    align_p.add_argument("--apply", action="store_true", help="Rewrite A-text when mismatch is obvious")
    align_p.add_argument("--also-fix-minor", action="store_true", help="Also rewrite when verdict is minor")
    align_p.add_argument("--dry-run", action="store_true", help="Do not write files (still calls LLM)")
    align_p.add_argument("--no-validate", action="store_true", help="Skip script_validation after applying fixes")
    
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ch = args.channel.upper()
    no = args.video.zfill(3)
    title = args.title

    # Lockdown policy (default ON): forbid ad-hoc model overrides that cause drift across agents.
    assert_no_llm_model_overrides(context="script_pipeline.cli (startup)")
    assert_task_overrides_unchanged(context="script_pipeline.cli (startup)")

    # Apply optional LLM overrides for this CLI process.
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
            # If the user passed a pure integer, treat it as slot selection for compatibility.
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

    # Optional per-task option overrides (debug only; requires emergency override under lockdown).
    task_opts: dict[str, dict[str, Any]] = {}

    if getattr(args, "llm_task_temperature", None):
        for raw in args.llm_task_temperature or []:
            spec = str(raw).strip()
            if not spec:
                continue
            if "=" not in spec:
                raise SystemExit(f"--llm-task-temperature must be TASK=FLOAT; got: {spec}")
            task, temp = spec.split("=", 1)
            task = task.strip()
            temp = temp.strip()
            if not task:
                raise SystemExit(f"--llm-task-temperature task is empty: {spec}")
            try:
                tval = float(temp)
            except Exception:
                raise SystemExit(f"--llm-task-temperature value must be FLOAT; got: {spec}")
            task_opts.setdefault(task, {})["temperature"] = tval

    if getattr(args, "llm_task_option", None):
        def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
            out: dict[str, Any] = dict(base or {})
            for k, v in (override or {}).items():
                if isinstance(v, dict) and isinstance(out.get(k), dict):
                    out[k] = _deep_merge(out.get(k) or {}, v)  # type: ignore[arg-type]
                else:
                    out[k] = v
            return out

        for raw in args.llm_task_option or []:
            spec = str(raw).strip()
            if not spec:
                continue
            if "=" not in spec:
                raise SystemExit(f"--llm-task-option must be TASK=JSON_OBJECT; got: {spec}")
            task, raw_json = spec.split("=", 1)
            task = task.strip()
            raw_json = raw_json.strip()
            if not task:
                raise SystemExit(f"--llm-task-option task is empty: {spec}")
            try:
                parsed = json.loads(raw_json)
            except Exception as e:
                raise SystemExit(f"--llm-task-option JSON parse failed for task={task}: {e}")
            if not isinstance(parsed, dict):
                raise SystemExit(f"--llm-task-option must be a JSON object for task={task}; got: {type(parsed).__name__}")
            cur = task_opts.get(task) or {}
            task_opts[task] = _deep_merge(cur, parsed)

    if task_opts:
        if lockdown_active():
            raise SystemExit(
                "Forbidden: --llm-task-temperature/--llm-task-option under YTM_ROUTING_LOCKDOWN=1. "
                "Use numeric slots (LLM_MODEL_SLOT) and SSOT task routing instead, or set YTM_EMERGENCY_OVERRIDE=1 for one-off debugging."
            )
        os.environ["LLM_FORCE_TASK_OPTIONS_JSON"] = json.dumps(task_opts, ensure_ascii=False)

    if args.command == "a-text-rebuild":
        _sync_episode_targets_from_sources(ch, no)
        from .runner import rebuild_a_text_from_patterns

        out = rebuild_a_text_from_patterns(
            ch,
            no,
            title=title,
            reason=str(getattr(args, "reason", "") or "manual_rebuild").strip() or "manual_rebuild",
        )
        if not bool(getattr(args, "no_validate", False)):
            st = run_stage(ch, no, "script_validation", title=title)
            out = {
                **out,
                "script_validation_status": (
                    st.stages.get("script_validation").status if st.stages.get("script_validation") else None
                ),
                "script_status": st.status,
            }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    if args.command == "audio":
        import subprocess
        
        # Resolve paths via SSOT
        from factory_common.paths import repo_root, video_root, audio_final_dir

        base_dir = repo_root()
        packages_dir = base_dir / "packages"
        content_dir = video_root(ch, no) / "content"
        human_path = content_dir / "assembled_human.md"
        assembled_path = content_dir / "assembled.md"
        input_path = human_path if human_path.exists() else assembled_path

        # Safety: require script_validation before TTS unless explicitly overridden.
        if not getattr(args, "allow_unvalidated", False):
            try:
                st = load_status(ch, no)
            except Exception as exc:
                raise SystemExit(f"Error: status.json not found for {ch}-{no}: {exc}") from exc
            sv = st.stages.get("script_validation")
            if sv is None or sv.status != "completed":
                raise SystemExit(
                    f"Error: script_validation is not completed for {ch}-{no}. "
                    f"Run: python -m script_pipeline.cli run --channel {ch} --video {no} --stage script_validation "
                    f"(or pass --allow-unvalidated to override)."
                )
        
        if not input_path.exists():
            raise SystemExit(f"Error: Input file not found: {input_path}")

        cmd = [
            sys.executable,
            "-m",
            "audio_tts.scripts.run_tts",
            "--channel", ch,
            "--video", no,
            "--input", str(input_path),
        ]
        if getattr(args, "allow_unvalidated", False):
            cmd.append("--allow-unvalidated")
        if getattr(args, "force_overwrite_final", False):
            cmd.append("--force-overwrite-final")
        if getattr(args, "indices", ""):
            cmd.extend(["--indices", str(args.indices)])
        if getattr(args, "prepass", False):
            cmd.append("--prepass")

        # NOTE: Do not force --out-wav/--log here.
        # run_tts writes intermediates under workspaces/scripts/**/audio_prep/ and then syncs
        # outputs to workspaces/audio/final/ (downstream SoT).
        
        if args.resume:
            cmd.append("--resume")
            
        print(f"Running audio synthesis for {ch}-{no}...")
        
        # NOTE: Do NOT force PYTHONPATH for Homebrew Python.
        # Setting PYTHONPATH can hide /opt/homebrew/lib/pythonX.Y/site-packages
        # (e.g., PyYAML), breaking the STRICT auditor. `run_tts.py` bootstraps
        # repo paths on its own.
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)

        env.setdefault("VOICEPEAK_CLI_GLOBAL_LOCK", "1")
        env.setdefault("VOICEPEAK_CLI_COOLDOWN_SEC", "0.35")
        env.setdefault("VOICEPEAK_CLI_TIMEOUT_SEC", "45")
        env.setdefault("VOICEPEAK_CLI_RETRY_COUNT", "4")
        env.setdefault("VOICEPEAK_CLI_RETRY_SLEEP_SEC", "0.5")

        max_retries = 0
        try:
            max_retries = int(str(env.get("YTM_AUDIO_RETRY_COUNT") or "2").strip() or "2")
        except Exception:
            max_retries = 2
        max_retries = max(0, min(10, max_retries))

        last_rc = 1
        for attempt in range(max_retries + 1):
            cmd_try = list(cmd)
            if attempt > 0 and "--resume" not in cmd_try:
                cmd_try.append("--resume")
            try:
                subprocess.run(cmd_try, cwd=base_dir, env=env, check=True)
                return
            except subprocess.CalledProcessError as e:
                last_rc = int(e.returncode or 1)
                if attempt >= max_retries:
                    print(f"Audio synthesis failed with exit code {last_rc}")
                    sys.exit(last_rc)
                backoff = float(min(10.0, 1.5 * (attempt + 1)))
                print(f"Audio synthesis failed (exit={last_rc}); retrying in {backoff:.1f}s... ({attempt+1}/{max_retries})")
                time.sleep(backoff)
        return

    if args.command == "semantic-align":
        from .tools.semantic_alignment import run_semantic_alignment

        out = run_semantic_alignment(
            ch,
            no,
            apply=bool(getattr(args, "apply", False)),
            also_fix_minor=bool(getattr(args, "also_fix_minor", False)),
            dry_run=bool(getattr(args, "dry_run", False)),
            validate_after=(not bool(getattr(args, "no_validate", False))),
        )
        print(
            json.dumps(
                {
                    "channel": ch,
                    "video": no,
                    "verdict": out.verdict,
                    "applied": out.applied,
                    "report_path": str(out.report_path),
                    "a_text_path": str(out.canonical_path),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "init":
        if status_path(ch, no).exists():
            print(f"status.json already exists: {status_path(ch, no)}")
            return
        stage_names = [s.get("name") for s in _load_stage_defs() if s.get("name")]
        init_status(ch, no, title or f"{ch}-{no}", stage_names)
        print(f"initialized status: {status_path(ch, no)}")
        return

    if args.command == "run":
        _sync_episode_targets_from_sources(ch, no)
        st = run_stage(ch, no, args.stage, title=title)
        print(f"ran stage: {args.stage}")
        pprint(st.__dict__)
        return

    if args.command == "run-all":
        _sync_episode_targets_from_sources(ch, no)
        max_iter = args.max_iter
        last_pending = None
        for i in range(max_iter):
            st = run_next(ch, no, title=title)
            pending = [name for name, s in st.stages.items() if s.status != "completed"]
            print(f"[{i+1}/{max_iter}] status={st.status}, pending={pending}")
            if st.status == "completed" or not pending:
                print("completed.")
                pprint(st.__dict__)
                return
            if pending == last_pending:
                print("no progress; stopping early.")
                pprint(st.__dict__)
                return
            last_pending = pending
        print("max iterations reached.")
        pprint(st.__dict__)
        return

    if args.command == "next":
        _sync_episode_targets_from_sources(ch, no)
        st = run_next(ch, no, title=title)
        pprint(st.__dict__)
        return

    if args.command == "status":
        p = status_path(ch, no)
        if not p.exists():
            print(f"status.json not found: {p}")
            return
        print(p.read_text(encoding="utf-8"))
        return

    if args.command == "validate":
        _sync_episode_targets_from_sources(ch, no)
        from .validator import validate_stage

        errors = validate_stage(ch, no, _load_stage_defs())
        if errors:
            print("❌ validation failed")
            for e in errors:
                print(" -", e)
        else:
            print("✅ validation passed")
        return

    if args.command == "reconcile":
        from .runner import reconcile_status

        st = reconcile_status(ch, no, allow_downgrade=bool(getattr(args, "allow_downgrade", False)))
        print("reconciled status:")
        pprint(st.__dict__)
        return

    if args.command == "reset":
        from .runner import reset_video

        st = reset_video(ch, no, wipe_research=bool(args.wipe_research))
        print(f"reset done: {status_path(ch, no)}")
        pprint(st.__dict__)
        return


if __name__ == "__main__":
    main()

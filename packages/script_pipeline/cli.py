#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from pprint import pprint

from .runner import run_stage, run_next
from .sot import init_status, load_status, status_path
from .runner import _load_stage_defs  # internal use for init ordering
from .sot import DATA_ROOT


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
        help="Force LLM router model key(s) for this run (comma-separated). Can be repeated.",
    )
    common.add_argument(
        "--llm-task-model",
        action="append",
        help="Per-task LLM override: TASK=MODELKEY[,MODELKEY...] (repeatable).",
    )

    sub.add_parser("init", parents=[common], help="Initialize status.json if missing")
    run_p = sub.add_parser("run", parents=[common], help="Run specific stage")
    run_p.add_argument("--stage", required=True, help="Stage name to run")
    sub.add_parser("next", parents=[common], help="Run the first pending stage")
    run_all_p = sub.add_parser("run-all", parents=[common], help="Run pending stages until completion or limit")
    run_all_p.add_argument("--max-iter", type=int, default=30, help="Maximum stage executions (default: 30)")
    sub.add_parser("status", parents=[common], help="Show status.json")
    sub.add_parser("validate", parents=[common], help="Validate required outputs")
    sub.add_parser("reconcile", parents=[common], help="Reconcile status based on existing outputs")
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
        "--allow-unvalidated",
        action="store_true",
        help="Allow TTS even when script_validation is not completed (not recommended).",
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

    # Apply optional LLM overrides for this CLI process.
    if getattr(args, "llm_model", None):
        flattened: list[str] = []
        for raw in args.llm_model or []:
            for part in str(raw).split(","):
                part = part.strip()
                if part:
                    flattened.append(part)
        if flattened:
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
            os.environ["LLM_FORCE_TASK_MODELS_JSON"] = json.dumps(mapping, ensure_ascii=False)

    if args.command == "a-text-rebuild":
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
        input_path = assembled_path

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
        
        # Ensure assembled.md exists when only assembled_human.md is present (human is authoritative; assembled is mirror).
        if not assembled_path.exists() and human_path.exists():
            try:
                assembled_path.parent.mkdir(parents=True, exist_ok=True)
                assembled_path.write_text(human_path.read_text(encoding="utf-8"), encoding="utf-8")
            except Exception as exc:
                raise SystemExit(
                    f"Error: Failed to materialize {assembled_path} from {human_path}: {exc}"
                ) from exc

        if not input_path.exists():
            print(f"Error: Input file not found: {input_path} (and no {human_path} to mirror)")
            return

        cmd = [
            sys.executable,
            "-m",
            "audio_tts_v2.scripts.run_tts",
            "--channel", ch,
            "--video", no,
            "--input", str(input_path),
        ]
        if getattr(args, "allow_unvalidated", False):
            cmd.append("--allow-unvalidated")

        # Write to artifacts/final by default so CapCut/preview always use latest audio/SRT.
        final_dir = audio_final_dir(ch, no)
        final_dir.mkdir(parents=True, exist_ok=True)
        final_wav = final_dir / f"{ch}-{no}.wav"
        final_log = final_dir / "log.json"
        cmd.extend(["--out-wav", str(final_wav), "--log", str(final_log)])
        
        if args.resume:
            cmd.append("--resume")
            
        print(f"Running audio synthesis for {ch}-{no}...")
        
        # NOTE: Do NOT force PYTHONPATH for Homebrew Python.
        # Setting PYTHONPATH can hide /opt/homebrew/lib/pythonX.Y/site-packages
        # (e.g., PyYAML), breaking the STRICT auditor. `run_tts.py` bootstraps
        # repo paths on its own.
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)

        try:
            subprocess.run(cmd, cwd=base_dir, env=env, check=True)
        except subprocess.CalledProcessError as e:
            print(f"Audio synthesis failed with exit code {e.returncode}")
            sys.exit(e.returncode)
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
        st = run_stage(ch, no, args.stage, title=title)
        print(f"ran stage: {args.stage}")
        pprint(st.__dict__)
        return

    if args.command == "run-all":
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

        st = reconcile_status(ch, no)
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

#!/usr/bin/env python3
from __future__ import annotations

import argparse
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
    
    # Audio command wrapper
    audio_p = sub.add_parser("audio", parents=[common], help="Run audio synthesis")
    audio_p.add_argument("--resume", action="store_true", help="Resume from existing chunks")
    
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ch = args.channel.upper()
    no = args.video.zfill(3)
    title = args.title

    if args.command == "audio":
        import subprocess
        
        # Resolve paths via SSOT
        from factory_common.paths import repo_root, video_root, audio_final_dir

        base_dir = repo_root()
        packages_dir = base_dir / "packages"
        input_path = video_root(ch, no) / "content" / "assembled.md"
        
        if not input_path.exists():
            print(f"Error: Input file not found: {input_path}")
            return

        cmd = [
            sys.executable,
            "-m",
            "audio_tts_v2.scripts.run_tts",
            "--channel", ch,
            "--video", no,
            "--input", str(input_path),
        ]

        # Write to artifacts/final by default so CapCut/preview always use latest audio/SRT.
        final_dir = audio_final_dir(ch, no)
        final_dir.mkdir(parents=True, exist_ok=True)
        final_wav = final_dir / f"{ch}-{no}.wav"
        final_log = final_dir / "log.json"
        cmd.extend(["--out-wav", str(final_wav), "--log", str(final_log)])
        
        if args.resume:
            cmd.append("--resume")
            
        print(f"Running audio synthesis for {ch}-{no}...")
        
        # Ensure PYTHONPATH includes root and audio_tts_v2
        env = os.environ.copy()
        pythonpath = env.get("PYTHONPATH", "")
        # Add repo root and `packages/` so monorepo imports work without root symlinks.
        new_paths = [str(base_dir), str(packages_dir)]
        if pythonpath:
            new_paths.append(pythonpath)
        env["PYTHONPATH"] = os.pathsep.join(new_paths)

        try:
            subprocess.run(cmd, cwd=base_dir, env=env, check=True)
        except subprocess.CalledProcessError as e:
            print(f"Audio synthesis failed with exit code {e.returncode}")
            sys.exit(e.returncode)
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

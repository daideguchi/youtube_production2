from __future__ import annotations

import argparse
from pathlib import Path
import os
import dotenv
import requests
import sys
import shutil

# Ensure project root and audio_tts_v2 are in sys.path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_TTS_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_TTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_TTS_ROOT))

# New Strict Orchestrator (Proposed)
# from tts.strict_orchestrator import run_strict_pipeline
from tts.routing import load_routing_config, decide_engine

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run audio_tts_v2 STRICT pipeline")
    p.add_argument("--channel", required=True, help="Channel ID (e.g. CH05)")
    p.add_argument("--video", required=True, help="Video ID (e.g. 001)")
    p.add_argument("--input", required=True, type=Path, help="Input assembled.md path")
    
    # Optional overrides
    p.add_argument("--out-wav", type=Path, help="Output WAV path override")
    p.add_argument("--log", type=Path, help="Output Log path override")
    p.add_argument("--engine-override", choices=["voicevox", "voicepeak", "elevenlabs"], help="Force specific engine")
    
    # LLM Settings (Now managed via .env and Router)
    p.add_argument("--llm-model", help="[Deprecated] LLM model key (Ignored, uses Router)")
    p.add_argument("--llm-api-key", help="[Deprecated] LLM API Key (Ignored, uses Router)")
    p.add_argument("--llm-timeout", type=int, default=120)

    # Voicepeak specific (Optional, maybe move to config later)
    p.add_argument("--voicepeak-narrator")
    p.add_argument("--voicepeak-speed", type=int, help="Voicepeak speed (50-200)")
    p.add_argument("--voicepeak-pitch", type=int, help="Voicepeak pitch (-300 to 300)")
    p.add_argument("--voicepeak-emotion", type=str, help="Voicepeak emotion (happy,sad,angry,fun)")
    
    # Partial Regeneration
    p.add_argument("--indices", type=str, help="Comma-separated segment indices to regenerate (0-based). Example: '3,10'")
    p.add_argument("--resume", action="store_true", help="Resume from existing chunks (skip generation if chunk exists)")
    # Prepass (reading only, no synthesis)
    p.add_argument("--prepass", action="store_true", help="Reading-only pass (no wav synthesis). Generates log.json with readings.")

    return p.parse_args()

def main() -> None:
    # Load .env
    dotenv.load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env", override=True)
    args = parse_args()

    if len(args.video) != 3 or not args.video.isdigit():
        raise SystemExit(f"Video number must be 3 digits (e.g., 001); got '{args.video}'")

    cfg = load_routing_config()
    
    # Determine Engine
    engine = args.engine_override or decide_engine(args.channel, args.video, cfg)
    print(f"[RUN] Channel={args.channel} Video={args.video} Engine={engine} StrictMode=ON")

    if engine == "voicevox":
        vv_url = cfg.voicevox_url
        try:
            r = requests.get(f"{vv_url}/speakers", timeout=3)
            r.raise_for_status()
        except Exception as e:
            raise SystemExit(f"[ERROR] Voicevox not reachable at {vv_url}: {e}")

    # IO Setup
    base_dir = Path(__file__).resolve().parents[2]
    if not args.input.exists():
        raise SystemExit(f"[ERROR] Input file not found: {args.input}")

    # --- Global guard: assemble the right script before synthesis -----------------
    # ルール:
    # - チャンネル/動画配下の content に assembled_human.md があれば、それが最終確定版。
    # - assembled_human.md が存在し、assembled.md と内容が異なる場合は、
    #   assembled_human.md を assembled.md に自動で同期してから進む。
    # - assembled_human.md が無ければ、assembled.md をそのまま使う。
    #
    # これにより「古い assembled.md を参照して誤って合成する」事故を防ぐ。
    content_dir = args.input.parent
    human_path = content_dir / "assembled_human.md"
    assembled_path = content_dir / "assembled.md"

    if args.input.name == "assembled.md" and human_path.exists():
        try:
            human_text = human_path.read_text(encoding="utf-8")
            assembled_text = assembled_path.read_text(encoding="utf-8") if assembled_path.exists() else ""
            if human_text != assembled_text:
                assembled_path.write_text(human_text, encoding="utf-8")
                print(f"[SYNC] assembled_human.md -> assembled.md (authoritative human edit detected)")
        except Exception as e:
            raise SystemExit(f"[ERROR] Failed to sync assembled_human.md -> assembled.md: {e}")

    # Output to script_pipeline/data/...
    artifact_root = base_dir / "script_pipeline" / "data" / args.channel / args.video / "audio_prep"

    def _latest_mtime(path: Path) -> float:
        mtimes = []
        if path.exists():
            mtimes.append(path.stat().st_mtime)
            for p in path.rglob("*"):
                if p.exists():
                    try:
                        mtimes.append(p.stat().st_mtime)
                    except OSError:
                        continue
        return max(mtimes) if mtimes else 0.0

    # If human/assembled script is newer than existing artifacts, purge audio_prep
    script_mtime = (human_path if human_path.exists() else assembled_path).stat().st_mtime
    if artifact_root.exists():
        artifacts_mtime = _latest_mtime(artifact_root)
        if script_mtime > artifacts_mtime:
            try:
                shutil.rmtree(artifact_root)
                print(f"[CLEAN] audio_prep purged (script newer than artifacts)")
            except Exception as e:
                raise SystemExit(f"[ERROR] Failed to purge old audio_prep: {e}")

    artifact_root.mkdir(parents=True, exist_ok=True)
    
    out_wav = args.out_wav or artifact_root / f"{args.channel}-{args.video}.wav"
    log_path = args.log or artifact_root / "log.json"
    
    # Voicepeak settings
    voicepeak_overrides = {
        k: v for k, v in {
            "narrator": args.voicepeak_narrator,
            "speed": args.voicepeak_speed,
            "pitch": args.voicepeak_pitch,
            "emotion": args.voicepeak_emotion,
        }.items() if v is not None
    }
    
    input_text = args.input.read_text(encoding="utf-8")

    # Import Lazy to avoid circular dependency if any
    from tts.strict_orchestrator import run_strict_pipeline

    try:
        # Strict Mode Pipeline
        run_strict_pipeline(
            channel=args.channel,
            video_no=args.video,
            input_text=input_text,
            output_wav=out_wav,
            output_log=log_path,
            engine=engine,
            voicepeak_config=voicepeak_overrides,
            artifact_root=out_wav.parent,
            target_indices=[int(i) for i in args.indices.split(",")] if args.indices else None,
            resume=args.resume,
            prepass=args.prepass,
        )
        if args.prepass:
            print(f"[SUCCESS] Prepass completed. Log: {log_path}")
        else:
            print(f"[SUCCESS] Pipeline completed. Output: {out_wav}")
        
        # Metadata
        from datetime import datetime
        meta_path = artifact_root / "inference_metadata.txt"
        meta_content = (
            f"inference_logic: v2_strict_router\n"
            f"model: router_managed\n"
            f"timestamp: {datetime.now().isoformat()}\n"
            "status: success\n"
        )
        meta_path.write_text(meta_content, encoding="utf-8")

    except Exception as e:
        print(f"[ERROR] Pipeline failed: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()

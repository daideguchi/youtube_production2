#!/usr/bin/env python3
"""
One-shot CapCut draft builder for any channel/SRT.

Usage:
    python3 tools/auto_capcut_run.py \
        --channel CH01 \
        --srt input/CH01_人生の道標/192.srt \
        --run-name jinsei192_v3 \
        --title "人生の道標 192話 ～タイトル～" \
        --labels "序章:導入,転機:気づき,対策:実行,結び:未来"

Requirements:
  - .env (root) loaded in the environment (OPENROUTER/GEMINI keys etc.)
  - Channel preset defined in config/channel_presets.json
  - Draft template exists under $HOME/Movies/CapCut/User Data/Projects/com.lveditor.draft
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
import json
import datetime
import time
import uuid

# Define PROJECT_ROOT before using it
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Import using the installed package structure
try:
    from commentary_02_srt2images_timeline.src.core.config import config
except ImportError:
    # Fallback to relative import if the package isn't properly installed
    import sys
    sys.path.append(str(PROJECT_ROOT))
    sys.path.append(str(PROJECT_ROOT / "src"))
    from src.core.config import config

def _truncate_summary(text: str, limit: int = 60) -> str:
    """Shorten text for LLM belt prompts; keep it safe for JSON."""
    if not text:
        return ""
    sanitized = " ".join(str(text).split())
    if len(sanitized) <= limit:
        return sanitized
    return sanitized[: max(0, limit - 1)] + "…"

DEFAULT_DRAFT_ROOT = Path.home() / "Movies/CapCut/User Data/Projects/com.lveditor.draft"


def run(cmd, env, cwd, exit_on_error=True, timeout=None, abort_patterns=None):
    print(f"▶ {' '.join(cmd)}")
    abort_patterns = [p.strip() for p in abort_patterns.split(",")] if abort_patterns else []
    start = time.time()
    proc = subprocess.Popen(cmd, env=env, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for line in proc.stdout:
            line = line.rstrip("\n")
            print(line)
            if abort_patterns and any(p in line for p in abort_patterns):
                print(f"❌ Abort pattern detected: {line}")
                proc.terminate()
                proc.wait(timeout=5)
                return subprocess.CompletedProcess(cmd, 1)
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.terminate()
        return subprocess.CompletedProcess(cmd, 124)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        proc.terminate()
        return subprocess.CompletedProcess(cmd, 1)

    elapsed = time.time() - start
    res = subprocess.CompletedProcess(cmd, proc.returncode)
    res.elapsed = elapsed  # attach elapsed seconds
    if res.returncode != 0 and exit_on_error:
        sys.exit(res.returncode)
    return res


def make_equal_split_belt(run_dir: Path, labels: str, opening_offset: float = 0.0):
    labels_list = [x.strip() for x in labels.split(",") if x.strip()]
    if not labels_list:
        labels_list = ["序章", "転機", "対策", "結び"]
    sections = len(labels_list)
    cues_path = run_dir / "image_cues.json"
    if not cues_path.exists():
        raise FileNotFoundError(f"image_cues.json not found in {run_dir}")
    cues = json.loads(cues_path.read_text(encoding="utf-8"))
    cues_list = cues.get("cues", [])
    total_duration = max((c.get("end_sec", 0) for c in cues_list), default=0.0)
    span = total_duration / sections if sections else total_duration
    belts = []
    for i, label in enumerate(labels_list):
        start = span * i
        end = total_duration if i == sections - 1 else span * (i + 1)
        belts.append({"text": label, "start": round(start, 3), "end": round(end, 3)})
    out = {"episode": "", "total_duration": round(total_duration, 3), "belts": belts, "opening_offset": opening_offset}
    (run_dir / "belt_config.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def make_llm_belt_from_cues(run_dir: Path, opening_offset: float = 0.0, sections: int = 4, channel_id: str = "CH02"):
    """
    Generate belt_config.json dynamically from image_cues.json using LLMRouter.
    Falls back to empty config on failure to allow pipeline to continue.
    """
    import json
    from pathlib import Path
    import logging

    # Extract video ID from run_dir path for logging purposes
    video_id = run_dir.name  # e.g. CH02-015_20251210_191904

    # Import the new belt generator module
    from commentary_02_srt2images_timeline.src.srt2images.belt_generator import generate_belt_from_script

    cues_path = run_dir / "image_cues.json"

    # Generate belt config using the new module
    belt_config = generate_belt_from_script(cues_path, opening_offset, sections, channel_id)

    if belt_config is None:
        # On failure, create an empty config to allow pipeline to continue
        logger = logging.getLogger(__name__)
        logger.warning(f"[{video_id}][belt_generation][FALLBACK] reason='belt generation returned None' sections=0")
        belt_config = {
            "episode": "",
            "total_duration": 0.0,
            "belts": [],
            "opening_offset": opening_offset
        }
    else:
        # Log the result appropriately
        belts_count = len(belt_config.get("belts", []))
        logger = logging.getLogger(__name__)
        if belts_count == 0:
            logger.warning(f"[{video_id}][belt_generation][FALLBACK] reason='belt generation returned empty config' sections=0")
        else:
            sample_text = belt_config["belts"][0]["text"][:50] if belt_config["belts"] else ""
            logger.info(f"[{video_id}][belt_generation][OK] sections={belts_count} sample_title='{sample_text}'")

    # Write the belt config to file
    (run_dir / "belt_config.json").write_text(
        json.dumps(belt_config, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    return belt_config


def generate_title_from_cues(cues_path: Path) -> str:
    if not cues_path.exists():
        raise FileNotFoundError(f"image_cues.json not found at {cues_path}")
    import json as _json
    cues_data = _json.loads(cues_path.read_text(encoding="utf-8"))
    cues = cues_data.get("cues") or []
    if not cues:
        raise ValueError("No cues to build title from.")

    summaries = []
    for c in cues:
        summary = c.get("summary") or c.get("text") or ""
        vf = c.get("visual_focus") or c.get("summary") or ""
        if summary and vf:
            summaries.append(f"{summary}｜視覚:{vf}")
        elif summary:
            summaries.append(summary)
        elif vf:
            summaries.append(vf)
    joined = "\n".join(summaries[:30])

    prompt = (
        "You are a Japanese copywriter. Read the following scene summaries and generate a concise Japanese YouTube title.\n"
        "Requirements:\n"
        "- Length: 18-28 Japanese characters\n"
        "- Tone: calm, trustworthy, warm\n"
        "- No quotes, no brackets\n"
        "- Avoid repeating words; keep it natural\n"
        "Scene summaries:\n"
    )
    content = prompt + joined

    # Use LLMRouter instead of direct google.genai
    try:
        from factory_common.llm_router import get_router
        router = get_router()
        response = router.call(
            task="title_generation",  # This needs to be added to the config
            messages=[{"role": "user", "content": content}],
            temperature=0.4,
        )
        title = response.strip().splitlines()[0] if response else ""
        if not title:
            raise RuntimeError("LLM returned empty title")
        return title
    except Exception as e:
        print(f"⚠️  Title generation failed: {e}, using deterministic fallback")
        # Deterministic fallback: use最初のサマリを18-28文字でカット、なければファイル名
        fallback = ""
        if summaries:
            fallback = summaries[0][:28]
        if not fallback:
            fallback = cues_path.stem
        return fallback


def load_channel_preset(channel_id: str):
    cfg_path = PROJECT_ROOT / "config" / "channel_presets.json"
    if not cfg_path.exists():
        return {}
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    return data.get("channels", {}).get(channel_id, {})


def validate_preset_minimum(channel_id: str, preset: dict):
    """Fail fast if必須フィールドが不足（activeのみ厳格）"""
    if not preset:
        return False, f"preset missing for {channel_id}"
    if preset.get("status", "active") != "active":
        return True, ""
    if not preset.get("capcut_template"):
        return False, f"capcut_template missing for {channel_id}"
    return True, ""


def main():
    overall_start = time.time()
    ap = argparse.ArgumentParser(description="End-to-end CapCut draft builder")
    ap.add_argument("--channel", required=True, help="Channel ID (e.g., CH01)")
    ap.add_argument("--srt", required=True, help="Path to input SRT")
    ap.add_argument("--run-name", help="Output run directory name under output/ (default: <srtname>_<timestamp>)")
    ap.add_argument("--title", help="Title to set in CapCut draft (if omitted, LLM generates from cues)")
    ap.add_argument("--labels", help="Comma-separated 4 labels for belts (equal split)")
    ap.add_argument("--size", default="1920x1080", help="Output size, default 1920x1080")
    ap.add_argument("--imgdur", default="20", help="Target img duration (seconds)")
    ap.add_argument("--fps", default="30", help="FPS")
    ap.add_argument("--crossfade", default="0.5", help="Crossfade seconds")
    ap.add_argument("--draft-root", default=str(DEFAULT_DRAFT_ROOT), help="CapCut draft root")
    ap.add_argument("--template", help="Explicit CapCut template name (optional, otherwise preset capcut_template)")
    ap.add_argument("--prompt-template", help="Explicit prompt template path (optional, otherwise preset prompt_template)")
    ap.add_argument("--img-concurrency", type=int, default=1, help="Image generation concurrency (default: 1 for rate limit safety)")
    ap.add_argument("--nanobanana", default="direct", choices=["direct", "none"], help="Image generation mode (direct=ImageClient(Gemini), none=skip)")
    ap.add_argument("--force", action="store_true", help="Force regenerate images")
    ap.add_argument("--suppress-warnings", action="store_true", default=True, help="Suppress DeprecationWarnings from underlying libs")
    ap.add_argument("--dry-run", action="store_true", help="Only run pre-flight + logging without modifying drafts")
    ap.add_argument("--exit-on-error", action="store_true", default=True, help="Exit immediately on errors (default)")
    ap.add_argument("--sleep-after-generation", type=float, default=0.0, help="Optional sleep (sec) after pipeline before CapCut step")
    ap.add_argument("--resume", action="store_true", help="Skip pipeline and reuse existing run_dir (belt/draft/title only)")
    ap.add_argument("--fallback-if-missing-cues", action="store_true", help="In resume mode, if image_cues.json is missing, run pipeline instead of failing")
    ap.add_argument(
        "--belt-mode",
        choices=["existing", "equal", "grouped", "llm"],
        default="llm",
        help="Belt generation mode: existing (use belt_config.json as-is), equal (manual labels), grouped (chapters/episode_info), llm (from image_cues via LLM)",
    )
    ap.add_argument("--timeout-ms", type=int, default=300000, help="Timeout per command (ms)")
    ap.add_argument("--abort-on-log", help="Comma-separated patterns; abort if any appears in child stdout/stderr")
    args = ap.parse_args()

    if args.nanobanana not in ("direct", "none"):
        print(f"⚠️ nanobanana={args.nanobanana} is deprecated; falling back to 'direct'")
        args.nanobanana = "direct"

    # config has already populated os.environ
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{PROJECT_ROOT}:{PROJECT_ROOT / 'src'}"
    if args.suppress_warnings:
        env.setdefault("PYTHONWARNINGS", "ignore::DeprecationWarning")
    
    # Check SSOT key availability (config.GEMINI_API_KEY throws if missing, but check here for user feedback)
    try:
        _ = config.GEMINI_API_KEY
    except ValueError:
        print("❌ GEMINI_API_KEY not found. Set it in the project .env or your shell environment.")
        sys.exit(1)

    run_name = args.run_name
    if not run_name:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        run_name = f"{Path(args.srt).stem}_{ts}"
    run_dir = PROJECT_ROOT / "output" / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    draft_name = run_name if run_name.endswith("_draft") else f"{run_name}_draft"

    # Copy SRT into run_dir for later CapCut insertion
    srt_basename = Path(args.srt).name
    srt_copy = run_dir / srt_basename
    if not srt_copy.exists():
        srt_copy.write_bytes(Path(args.srt).read_bytes())

    # Load preset for opening_offset (and potential future defaults)
    preset = load_channel_preset(args.channel) or {}
    belt_cfg = preset.get("belt", {}) if isinstance(preset, dict) else {}
    opening_offset = float(belt_cfg.get("opening_offset", 0.0))
    template_override = args.template or preset.get("capcut_template") or ""
    prompt_template_override = args.prompt_template or preset.get("prompt_template") or ""
    if prompt_template_override:
        pt = Path(prompt_template_override)
        if not pt.is_absolute():
            clean = str(pt)
            if clean.startswith("templates/"):
                clean = clean[len("templates/") :]
            elif clean.startswith("/templates/"):
                clean = clean[len("/templates/") :]
            pt = (PROJECT_ROOT / "templates" / clean).resolve()
        else:
            pt = pt.resolve()
        if not pt.exists():
            print(f"❌ prompt_template not found: {pt}")
            sys.exit(1)
        prompt_template_override = str(pt)

    ok, msg = validate_preset_minimum(args.channel, preset)
    if not ok:
        print(f"❌ Preset invalid: {msg}")
        sys.exit(1)

    # Fast-fail if CapCut template is missing on disk to avoid long waits later
    if template_override:
        template_path = Path(args.draft_root) / template_override
        if not template_path.exists():
            print(
                f"❌ CapCut template not found: {template_path}\n"
                f"   - draft_root = {args.draft_root}\n"
                f"   - template   = {template_override}\n"
                "テンプレートを配置するか、--template で存在する名前を指定してください。"
            )
            sys.exit(1)

    # 1) run_pipeline (images + cues) unless resume
    need_pipeline = not args.resume
    cues_path = run_dir / "image_cues.json"
    if args.resume and args.fallback_if_missing_cues and not cues_path.exists():
        print("ℹ️ resume requested but image_cues.json missing; running pipeline instead")
        need_pipeline = True

    if need_pipeline:
        # Resolve run_pipeline.py relative to this script to avoid hardcoding CWD assumptions
        tools_dir = Path(__file__).resolve().parent
        run_pipeline_path = tools_dir / "run_pipeline.py"
        
        pipeline_cmd = [
            sys.executable,
            str(run_pipeline_path),
            "--srt",
            str(args.srt),
            "--out",
            str(run_dir),
            "--engine",
            "none",
            "--size",
            args.size,
            "--imgdur",
            args.imgdur,
            "--cue-mode",
            "grouped",
            "--crossfade",
            args.crossfade,
            "--fps",
            args.fps,
            "--nanobanana",
            args.nanobanana,
            "--use-aspect-guide",
        ]
        if args.force:
            pipeline_cmd.append("--force")
        pipeline_cmd += [
            "--channel",
            args.channel,
            "--concurrency",
            str(args.img_concurrency),
        ]
        if prompt_template_override:
            pipeline_cmd += ["--prompt-template", prompt_template_override]
        # Run pipeline; on dry-run, disable generation to avoid API calls
        if args.dry_run:
            pipeline_cmd = [x for x in pipeline_cmd if x not in ("--nanobanana", args.nanobanana)]
            pipeline_cmd += ["--nanobanana", "none"]
            pipeline_cmd = [x for x in pipeline_cmd if x != "--force"]
        pipeline_res = run(
            pipeline_cmd,
            env,
            PROJECT_ROOT,
            exit_on_error=args.exit_on_error,
            timeout=args.timeout_ms / 1000 if args.timeout_ms else None,
            abort_patterns=args.abort_on_log,
        )
    else:
        pipeline_res = None
        print("ℹ️ resume mode: skipping pipeline (reuse existing cues/images)")

    # 2) belt_config generation (optional)
    labels = args.labels or preset.get("belt_labels") or ""

    def validate_labels_text(label_str: str):
        parts = [x.strip() for x in label_str.split(",") if x.strip()]
        if not parts:
            print("❌ labels are empty; provide 4 Japanese labels (例: 序章,転機,対策,結び)")
            sys.exit(1)
        if len(parts) != 4:
            print(f"⚠️ labels count is {len(parts)} (expected 4). Proceeding but belt alignment may be off.")
        if re.search(r"[A-Za-z]", label_str):
            print("❌ labels contain ASCII letters. 帯の文言は日本語で指定してください。")
            sys.exit(1)
        return parts

    if args.belt_mode == "equal":
        if not labels:
            print("❌ labels required for belt generation (equal).")
            sys.exit(1)
        validate_labels_text(labels)

    def build_equal():
        make_equal_split_belt(run_dir, labels, opening_offset=opening_offset)

    if args.belt_mode == "existing":
        if not (run_dir / "belt_config.json").exists():
            print("ℹ️ belt_mode=existing: belt_config.json not found, skipping belt generation.")
    elif args.belt_mode == "equal":
        build_equal()
    elif args.belt_mode == "grouped":
        # require chapters.json + episode_info.json; no fallback allowed
        chapters = run_dir / "chapters.json"
        epi = run_dir / "episode_info.json"
        if not (chapters.exists() and epi.exists()):
            print("❌ grouped belt requires chapters.json and episode_info.json. Prepare them in the run_dir and retry.")
            sys.exit(1)

        belt_cmd = [
            sys.executable,
            "tools/generate_belt_layers.py",
            "--episode-info",
            str(epi),
            "--chapters",
            str(chapters),
            "--output",
            str(run_dir / "belt_config.json"),
            "--opening-offset",
            str(opening_offset),
        ]
        if labels:
            belt_cmd += [
                "--sections",
                str(len(labels.split(","))),
                "--labels",
                labels,
            ]
        run(belt_cmd, env, PROJECT_ROOT, exit_on_error=args.exit_on_error, timeout=args.timeout_ms / 1000 if args.timeout_ms else None, abort_patterns=args.abort_on_log)
    elif args.belt_mode == "llm":
        make_llm_belt_from_cues(run_dir, opening_offset=opening_offset)
    else:
        print(f"❌ Unknown belt_mode: {args.belt_mode}")
        sys.exit(1)

    if args.dry_run:
        print("✅ Dry-run: Skipped draft build and title injection")
        return

    if args.sleep_after_generation > 0:
        time.sleep(args.sleep_after_generation)

    # 3) CapCut draft build
    # Generate title via LLM if not provided
    generated_title = None
    if not args.title:
        try:
            cues_path = run_dir / "image_cues.json"
            generated_title = generate_title_from_cues(cues_path)
            print(f"📝 Generated title via LLM: {generated_title}")
        except Exception as e:
            print(f"❌ Title generation failed: {e}")
            sys.exit(1)
    effective_title = args.title or generated_title or Path(args.srt).stem

    # Ensure belt_config (if exists) carries the effective title as main belt
    belt_path = run_dir / "belt_config.json"
    if belt_path.exists():
        try:
            belt_data = json.loads(belt_path.read_text(encoding="utf-8"))
            if isinstance(belt_data, dict):
                if "belt_lower" in belt_data and isinstance(belt_data["belt_lower"], dict):
                    belt_data["belt_lower"]["text"] = effective_title
                belt_data.setdefault("main_title", effective_title)
                belt_path.write_text(json.dumps(belt_data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"⚠️ Failed to update belt_config with title: {e}")

    draft_cmd = [
        sys.executable,
        "tools/capcut_bulk_insert.py",
        "--run",
        str(run_dir),
        "--draft-root",
        str(Path(args.draft_root)),
        "--channel",
        args.channel,
        "--template",
        template_override,  # resolver will auto-pick from preset if empty
        "--new",
        draft_name,
        "--skip-title",  # title will be injected via JSON to avoid constructor issues
        "--title",
        effective_title,
        "--srt-file",
        str(srt_copy),
        "--belt-config",
        str(run_dir / "belt_config.json"),
        "--tx",
        "0.0",
        "--ty",
        "0.0",
        "--scale",
        "1.03",
        "--crossfade",
        args.crossfade,
        "--opening-offset",
        str(opening_offset),
        "--rank-from-top",
        "4",
    ]
    draft_res = run(draft_cmd, env, PROJECT_ROOT, exit_on_error=args.exit_on_error, timeout=args.timeout_ms / 1000 if args.timeout_ms else None, abort_patterns=args.abort_on_log)

    # 4) Inject title via JSON (robust)
    inject_cmd = [
        sys.executable,
        "tools/inject_title_json.py",
        "--draft",
        str(Path(args.draft_root) / draft_name),
        "--title",
        effective_title,
        "--duration",
        "30",
    ]
    inject_res = run(inject_cmd, env, PROJECT_ROOT, exit_on_error=args.exit_on_error, timeout=args.timeout_ms / 1000 if args.timeout_ms else None, abort_patterns=args.abort_on_log)

    # Write a small run log
    log_path = run_dir / "auto_run_info.json"
    try:
        existing = {}
        if log_path.exists():
            existing = json.loads(log_path.read_text(encoding="utf-8"))
    except Exception:
        existing = {}

    log = existing if isinstance(existing, dict) else {}
    log.update({
        "channel": args.channel,
        "srt": str(Path(args.srt).resolve()),
        "run_dir": str(run_dir),
        "draft": str(Path(args.draft_root) / draft_name),
        "draft_name": draft_name,
        "labels": labels,
        "opening_offset": opening_offset,
        "nanobanana": args.nanobanana,
        "force": args.force,
        "template": template_override,
        "belt_mode": args.belt_mode,
        "resume": args.resume,
        "fallback_if_missing_cues": args.fallback_if_missing_cues,
        "timeout_ms": args.timeout_ms,
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
    })

    # Summarize cues/images
    try:
        cues = json.loads((run_dir / "image_cues.json").read_text(encoding="utf-8"))
        cue_count = len(cues.get("cues", []))
        total_duration = max((c.get("end_sec", 0) for c in cues.get("cues", [])), default=0.0)
        log["images"] = cue_count
        log["duration_sec"] = total_duration
    except Exception:
        cue_count = 0
        total_duration = 0.0

    # record timings if available
    timings = {}
    for name, res in (
        ("pipeline", locals().get("pipeline_res")),
        ("draft", locals().get("draft_res")),
        ("title", locals().get("inject_res")),
    ):
        if res is not None and hasattr(res, "elapsed"):
            timings[f"{name}_seconds"] = round(res.elapsed, 2)
    overall_elapsed = time.time() - overall_start
    timings["overall_seconds"] = round(overall_elapsed, 2)
    if timings:
        log.setdefault("timings", {}).update(timings)

    log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")

    print("✅ Complete. Draft at:", log["draft"])
    print(f"   Images: {cue_count} | Duration: {total_duration/60:.1f} min | Labels: {labels}")
    if generated_title:
        print(f"   Title (LLM): {generated_title}")


if __name__ == "__main__":
    main()

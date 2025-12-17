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
import csv
import os
import re
import subprocess
import sys
from pathlib import Path
import json
import datetime
import time
import uuid

def _bootstrap_repo_root() -> Path:
    start = Path(__file__).resolve()
    cur = start if start.is_dir() else start.parent
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
    return cur


_BOOTSTRAP_REPO = _bootstrap_repo_root()
if str(_BOOTSTRAP_REPO) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_REPO))

from factory_common.paths import (  # noqa: E402
    audio_artifacts_root,
    channels_csv_path,
    repo_root,
    video_capcut_local_drafts_root,
    video_pkg_root,
    video_runs_root,
)

PROJECT_ROOT = video_pkg_root()
REPO_ROOT = repo_root()

from factory_common.timeline_manifest import (
    EpisodeId,
    build_timeline_manifest,
    parse_episode_id,
    resolve_final_audio_srt,
    write_timeline_manifest,
)

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
FALLBACK_LOCAL_DRAFT_ROOT = video_capcut_local_drafts_root()


def _ensure_writable_draft_root(
    draft_root: Path,
    *,
    template_name: str,
    default_root: Path = DEFAULT_DRAFT_ROOT,
    fallback_root: Path = FALLBACK_LOCAL_DRAFT_ROOT,
) -> Path:
    """
    Ensure draft_root is writable.

    On environments without macOS Full Disk Access, $HOME/Movies/CapCut/... is readable but not writable
    (Operation not permitted). In that case we fall back to a repo-local draft root so the pipeline can
    still produce drafts deterministically.

    Note: CapCut UI will NOT auto-pick drafts from the fallback root. Users must copy the folder into
    the CapCut draft root (or run this tool from a Terminal with Full Disk Access).
    """
    draft_root = draft_root.expanduser().resolve()
    default_root = default_root.expanduser().resolve()

    if draft_root != default_root:
        return draft_root

    if os.access(str(draft_root), os.W_OK):
        return draft_root

    fallback_root = fallback_root.expanduser().resolve()
    fallback_root.mkdir(parents=True, exist_ok=True)

    # Mirror template into fallback root (read-only from CapCut root is OK).
    src_template = default_root / template_name
    dst_template = fallback_root / template_name
    if template_name and not dst_template.exists():
        if not src_template.exists():
            raise FileNotFoundError(f"CapCut template not found: {src_template}")
        import shutil

        shutil.copytree(src_template, dst_template)
        print(f"ℹ️ Copied CapCut template into local draft root: {dst_template}")

    print(
        "ℹ️ CapCut draft root is not writable in this environment. "
        f"Writing drafts to local root instead: {fallback_root}\n"
        "   To use in CapCut: copy the generated draft folder into "
        f"`{default_root}` (requires Full Disk Access)."
    )
    return fallback_root


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


def make_llm_belt_from_cues(run_dir: Path, opening_offset: float = 0.0, sections: int = 4, channel_id: str | None = None):
    """
    Generate belt_config.json dynamically from image_cues.json using LLMRouter.
    Falls back to empty config on failure to allow pipeline to continue.
    """
    import json
    from pathlib import Path
    import logging

    # Extract video ID from run_dir path for logging purposes
    video_id = run_dir.name  # e.g. CH02-015_20251210_191904
    if not channel_id:
        m = re.search(r"(CH\\d{2})", video_id, flags=re.IGNORECASE)
        channel_id = m.group(1).upper() if m else "CH02"
        logging.warning("belt_generation: channel_id not provided; inferred %s", channel_id)

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
        "Output ONLY the title on a single line. No explanation, no bullet points.\n"
        "Requirements:\n"
        "- Length: 18-28 Japanese characters\n"
        "- Tone: calm, trustworthy, warm\n"
        "- No quotes, no brackets\n"
        "- Avoid repeating words; keep it natural\n"
        "Scene summaries:\n"
    )
    content = prompt + joined

    # Disabling text LLM and falling back to heuristics is forbidden.
    # Use THINK/AGENT mode instead if you want to avoid API calls.
    if os.getenv("SRT2IMAGES_DISABLE_TEXT_LLM") == "1":
        raise RuntimeError(
            "SRT2IMAGES_DISABLE_TEXT_LLM=1 is set, but heuristic title fallback is forbidden. "
            "Unset it and rerun (or set LLM_MODE=think to use the agent queue)."
        )

    # Use LLMRouter instead of direct google.genai
    try:
        from factory_common.llm_router import get_router
        router = get_router()
        response = router.call(
            task="title_generation",  # This needs to be added to the config
            messages=[{"role": "user", "content": content}],
            temperature=0.4,
            max_tokens=128,
        )
        title = response.strip().splitlines()[0] if response else ""
        if not title:
            raise RuntimeError("LLM returned empty title")
        return title
    except Exception as e:
        raise RuntimeError(f"Title generation failed (no fallback): {e}") from e


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

def _load_planning_row(episode: EpisodeId) -> dict[str, str] | None:
    """
    Load the planning/progress CSV row for an episode.
    Prefers the unified SoT location via factory_common.paths.channels_csv_path().
    """
    csv_path = channels_csv_path(episode.channel)
    if not csv_path.exists():
        return None
    try:
        with csv_path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            target = f"{episode.channel.upper()}-{episode.video.zfill(3)}"
            for row in reader:
                if not isinstance(row, dict):
                    continue
                vid = (row.get("動画ID") or row.get("video_id") or "").strip()
                if vid.upper() != target:
                    continue
                return {k: (v or "").strip() for k, v in row.items() if k}
    except Exception:
        return None
    return None


def resolve_title_from_planning_csv(episode: EpisodeId) -> str | None:
    """
    Resolve the human title from planning CSV.
    This is the SoT for naming CapCut draft folders (LLM title generation is fallback only).
    """
    row = _load_planning_row(episode)
    if not row:
        return None
    title = (row.get("タイトル") or row.get("title") or "").strip()
    return title or None


def _normalize_belt_text(text: str) -> str:
    # Remove leading bracket tag like 【シニアの青春】 to keep belt concise.
    t = (text or "").strip()
    t = re.sub(r"^【[^】]+】", "", t).strip()
    t = re.sub(r"\\s+", " ", t).strip()
    # Soft wrap long single-line titles for better belt readability.
    if "\n" not in t and len(t) >= 34:
        # Prefer splitting on sentence punctuation near the middle.
        mid = len(t) // 2
        candidates = [m.start() for m in re.finditer(r"[。！？!\\?]|」", t)]
        if candidates:
            split_at = min(candidates, key=lambda i: abs(i - mid))
            if 4 <= split_at < len(t) - 2:
                t = t[: split_at + 1] + "\n" + t[split_at + 1 :].lstrip()
    return t


def resolve_belt_text_from_planning_csv(episode: EpisodeId) -> str | None:
    """
    Resolve main belt text from planning CSV (used for template's "メイン帯").
    Priority:
      1) サムネタイトル
      2) サムネタイトル上/下 (joined with newline)
      3) タイトル (normalized)
    """
    row = _load_planning_row(episode)
    if not row:
        return None
    thumb = (row.get("サムネタイトル") or "").strip()
    if thumb:
        return _normalize_belt_text(thumb)
    up = (row.get("サムネタイトル上") or "").strip()
    down = (row.get("サムネタイトル下") or "").strip()
    if up or down:
        joined = "\n".join([x for x in (up, down) if x])
        return _normalize_belt_text(joined)
    title = (row.get("タイトル") or "").strip()
    return _normalize_belt_text(title) if title else None


def main():
    overall_start = time.time()
    ap = argparse.ArgumentParser(description="End-to-end CapCut draft builder")
    ap.add_argument("--channel", required=True, help="Channel ID (e.g., CH01)")
    ap.add_argument("--srt", required=True, help="Path to input SRT")
    ap.add_argument(
        "--prefer-tts-final",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Prefer workspaces/audio/final/<CH>/<NNN>/<CH>-<NNN>.srt when resolvable (default: true)",
    )
    ap.add_argument(
        "--insert-audio",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Insert voice WAV into CapCut draft when available (default: true)",
    )
    ap.add_argument("--run-name", help="Output run directory name under output/ (default: <srtname>_<timestamp>)")
    ap.add_argument("--title", help="Title to set in CapCut draft (if omitted, LLM generates from cues)")
    ap.add_argument("--labels", help="Comma-separated 4 labels for belts (equal split)")
    ap.add_argument("--size", default="1920x1080", help="Output size, default 1920x1080")
    ap.add_argument("--imgdur", default="20", help="Target img duration (seconds)")
    ap.add_argument("--fps", default="30", help="FPS")
    ap.add_argument("--crossfade", default="0.5", help="Crossfade seconds")
    ap.add_argument("--scale", type=float, default=1.03, help="Global image scale (default: 1.03)")
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
        choices=["auto", "existing", "equal", "grouped", "llm"],
        default="auto",
        help="Belt generation mode: auto (preset-driven), existing (use belt_config.json as-is), equal (manual labels), grouped (chapters/episode_info), llm (from image_cues via LLM)",
    )
    # IMPORTANT: User requested no time-based interruption. Stop on abort patterns instead.
    ap.add_argument("--timeout-ms", type=int, default=0, help="Timeout per command (ms) (default: 0 = unlimited)")
    ap.add_argument("--abort-on-log", help="Comma-separated patterns; abort if any appears in child stdout/stderr")
    ap.add_argument(
        "--draft-name-policy",
        choices=["planning", "run"],
        default="planning",
        help="CapCut draft folder naming policy: planning (use workspaces/planning/channels CSV title) or run (use --run-name/_draft)",
    )
    ap.add_argument(
        "--draft-name-with-title",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Include '__<title>' suffix in CapCut draft directory name (default: true)",
    )
    args = ap.parse_args()

    if args.nanobanana not in ("direct", "none"):
        print(f"⚠️ nanobanana={args.nanobanana} is deprecated; falling back to 'direct'")
        args.nanobanana = "direct"

    # Safety: prevent cross-channel wiring.
    # If SRT filename/path implies another channel, fail fast.
    requested_srt_path = Path(args.srt).expanduser().resolve()
    name_match = re.search(r"(CH\d{2})", requested_srt_path.name, flags=re.IGNORECASE)
    if name_match and name_match.group(1).upper() != args.channel.upper():
        print(
            f"❌ channel mismatch: srt={requested_srt_path.name} implies {name_match.group(1).upper()} but --channel={args.channel}"
        )
        sys.exit(1)
    final_root = audio_artifacts_root() / "final"
    try:
        rel_parts = requested_srt_path.relative_to(final_root).parts
        if rel_parts:
            dir_ch = rel_parts[0][:4].upper()
            if dir_ch.startswith("CH") and dir_ch[2:4].isdigit() and dir_ch != args.channel.upper():
                print(f"❌ channel mismatch: srt under {dir_ch} but --channel={args.channel}")
                sys.exit(1)
    except Exception:
        pass

    # Resolve SoT SRT/WAV from audio_tts_v2 final when possible.
    episode = parse_episode_id(str(requested_srt_path))
    if episode is None and re.fullmatch(r"\d{1,3}", requested_srt_path.stem):
        episode = EpisodeId(channel=args.channel.upper(), video=requested_srt_path.stem.zfill(3))
    effective_srt_path = requested_srt_path
    effective_wav_path: Path | None = None
    if args.prefer_tts_final and episode and episode.channel.upper() == args.channel.upper():
        try:
            effective_wav_path, effective_srt_path = resolve_final_audio_srt(episode)
            if effective_srt_path.resolve() != requested_srt_path.resolve():
                print(f"[SoT] Using final SRT: {effective_srt_path} (requested: {requested_srt_path})")
        except FileNotFoundError:
            # Keep requested SRT (manual workflows / in-progress episodes)
            effective_srt_path = requested_srt_path
            effective_wav_path = None

    # config has already populated os.environ
    env = os.environ.copy()
    # Important: do NOT force PYTHONPATH for subprocesses.
    # On Homebrew Python this can flip sys.prefix to the Cellar path and hide
    # /opt/homebrew/lib/pythonX.Y/site-packages (e.g., pydantic), causing runtime failures.
    env.pop("PYTHONPATH", None)
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
        stem = effective_srt_path.stem
        # Avoid cross-channel collisions when SRT filename is numeric-only (e.g., 220.srt)
        if re.fullmatch(r"\d{1,3}", stem):
            stem = f"{args.channel}-{stem.zfill(3)}"
        run_name = f"{stem}_{ts}"
    run_dir = video_runs_root() / run_name
    run_dir.mkdir(parents=True, exist_ok=True)

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

    # macOS Full Disk Access workaround: default CapCut draft root may be read-only from this process.
    # If so, write into a local draft root and mirror the required template automatically.
    try:
        resolved_root = _ensure_writable_draft_root(Path(args.draft_root), template_name=template_override)
        args.draft_root = str(resolved_root)
    except Exception as e:
        print(f"❌ Failed to prepare draft root: {e}")
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

    # Sync SRT into run_dir for later CapCut insertion.
    # - If we (re)run pipeline, always overwrite to keep cues/subtitles aligned.
    # - If resume mode, do NOT overwrite; fail fast when a different SRT is passed to avoid unrelated drafts.
    srt_source = effective_srt_path
    srt_basename = f"{episode.episode}.srt" if episode else srt_source.name
    srt_copy = run_dir / srt_basename
    try:
        src_bytes = srt_source.read_bytes()
    except Exception as e:
        print(f"❌ Failed to read SRT: {srt_source} ({e})")
        sys.exit(1)

    if need_pipeline:
        # overwrite when different (or missing)
        try:
            if (not srt_copy.exists()) or (srt_copy.read_bytes() != src_bytes):
                srt_copy.write_bytes(src_bytes)
                print(f"[SYNC] run_dir SRT updated: {srt_copy.name}")
        except Exception:
            srt_copy.write_bytes(src_bytes)
            print(f"[SYNC] run_dir SRT overwritten: {srt_copy.name}")
    else:
        # resume: preserve existing copy; abort if mismatch
        if srt_copy.exists():
            try:
                if srt_copy.read_bytes() != src_bytes:
                    print(
                        "❌ Resume mode with different SRT detected. "
                        "Use a new --run-name or disable --resume to regenerate cues/images."
                    )
                    sys.exit(1)
            except Exception:
                pass
        else:
            # no existing copy; copy best-effort (assume args.srt matches cues)
            srt_copy.write_bytes(src_bytes)
            print(f"[SYNC] run_dir SRT copied (resume): {srt_copy.name}")

    if need_pipeline:
        # Resolve run_pipeline.py relative to this script to avoid hardcoding CWD assumptions
        tools_dir = Path(__file__).resolve().parent
        run_pipeline_path = tools_dir / "run_pipeline.py"
        
        pipeline_cmd = [
            sys.executable,
            str(run_pipeline_path),
            "--srt",
            str(effective_srt_path),
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

    resolved_belt_mode = args.belt_mode
    if resolved_belt_mode == "auto":
        belt_cfg = preset.get("belt", {}) or {}
        requires_cfg = bool(belt_cfg.get("requires_config", False))
        resolved_belt_mode = "llm" if requires_cfg else "existing"

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

    if resolved_belt_mode == "equal":
        if not labels:
            print("❌ labels required for belt generation (equal).")
            sys.exit(1)
        validate_labels_text(labels)

    def build_equal():
        make_equal_split_belt(run_dir, labels, opening_offset=opening_offset)

    if resolved_belt_mode == "existing":
        if not (run_dir / "belt_config.json").exists():
            print("ℹ️ belt_mode=existing: belt_config.json not found, skipping belt generation.")
    elif resolved_belt_mode == "equal":
        build_equal()
    elif resolved_belt_mode == "grouped":
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
    elif resolved_belt_mode == "llm":
        if os.getenv("SRT2IMAGES_DISABLE_TEXT_LLM") == "1":
            raise SystemExit(
                "SRT2IMAGES_DISABLE_TEXT_LLM=1 is set, but deterministic belt fallback is forbidden. "
                "Unset it and rerun, or use --belt-mode existing/equal/grouped (or LLM_MODE=think)."
            )
        make_llm_belt_from_cues(run_dir, opening_offset=opening_offset, sections=4, channel_id=args.channel)
    else:
        print(f"❌ Unknown belt_mode: {args.belt_mode}")
        sys.exit(1)

    if args.dry_run:
        print("✅ Dry-run: Skipped draft build")
        return

    if args.sleep_after_generation > 0:
        time.sleep(args.sleep_after_generation)

    # 3) CapCut draft build
    # Title sources (no LLM unless unavoidable):
    # - Draft folder naming: planning CSV `タイトル` (stable SoT)
    # - Main belt text: planning CSV `サムネタイトル` etc (prefer concise)
    # - Manual override: --title overrides belt text
    # - LLM title generation is fallback only when planning is missing
    generated_title = None
    planning_title = resolve_title_from_planning_csv(episode) if episode else None
    planning_belt_text = resolve_belt_text_from_planning_csv(episode) if episode else None
    if not args.title and not planning_belt_text:
        try:
            cues_path = run_dir / "image_cues.json"
            generated_title = generate_title_from_cues(cues_path)
            print(f"📝 Generated title via LLM: {generated_title}")
        except Exception as e:
            print(f"❌ Title generation failed: {e}")
            sys.exit(1)
    effective_belt_title = args.title or planning_belt_text or generated_title or Path(args.srt).stem

    def _sanitize_capcut_name(value: str, *, max_len: int = 220) -> str:
        safe = re.sub(r"[\\/:*?\"<>|]", "_", str(value))
        safe = " ".join(safe.split())
        safe = re.sub(r"[_\\s]+", "_", safe).strip("_")
        return safe[:max_len]

    # Draft directory name:
    # - planning: Prefer a stable SoT name from planning CSV when episode is resolvable.
    # - run: Always use run_name-based draft (regen/debug workflows).
    draft_name = ""
    if args.draft_name_policy == "planning" and episode and planning_title:
        draft_name = _sanitize_capcut_name(f"★{episode.episode}-{planning_title}")
    if not draft_name:
        draft_base = run_name if run_name.endswith("_draft") else f"{run_name}_draft"
        draft_name = draft_base
        if args.draft_name_with_title:
            safe = _sanitize_capcut_name(str(effective_belt_title), max_len=120)
            if safe:
                draft_name = f"{draft_base}__{safe}"

    # Ensure belt_config (if exists) carries the effective title as main belt
    belt_path = run_dir / "belt_config.json"
    if belt_path.exists():
        try:
            belt_data = json.loads(belt_path.read_text(encoding="utf-8"))
            if isinstance(belt_data, dict):
                if "belt_lower" in belt_data and isinstance(belt_data["belt_lower"], dict):
                    belt_data["belt_lower"]["text"] = effective_belt_title
                belt_data["main_title"] = effective_belt_title
                belt_path.write_text(json.dumps(belt_data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"⚠️ Failed to update belt_config with title: {e}")

    # Position/scale defaults from preset to avoid cross-channel wiring bugs.
    pos = preset.get("position", {}) if isinstance(preset, dict) else {}
    tx = str(pos.get("tx", 0.0))
    ty = str(pos.get("ty", 0.0))
    scale = str(pos.get("scale", args.scale))

    belt_arg = []
    if belt_path.exists():
        belt_arg = ["--belt-config", str(belt_path)]

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
        effective_belt_title,
        "--srt-file",
        str(srt_copy),
        *(["--voice-file", str(effective_wav_path)] if (args.insert_audio and effective_wav_path and not args.dry_run) else []),
        *belt_arg,
        "--tx",
        tx,
        "--ty",
        ty,
        "--scale",
        scale,
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
        effective_belt_title,
        "--duration",
        "30",
    ]
    inject_res = run(inject_cmd, env, PROJECT_ROOT, exit_on_error=args.exit_on_error, timeout=args.timeout_ms / 1000 if args.timeout_ms else None, abort_patterns=args.abort_on_log)

    # Create/refresh run_dir symlink to the draft for quick navigation
    if not args.dry_run:
        try:
            link = run_dir / "capcut_draft"
            if link.exists() or link.is_symlink():
                link.unlink()
            os.symlink(str((Path(args.draft_root) / draft_name).resolve()), str(link))
        except Exception:
            pass

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
        # Back-compat: keep the old key name as the effective SoT input
        "srt": str(effective_srt_path.resolve()),
        "srt_requested": str(requested_srt_path),
        "srt_effective": str(effective_srt_path.resolve()),
        "audio_wav_effective": str(effective_wav_path.resolve()) if effective_wav_path else "",
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

    # Write timeline manifest when final audio/srt are resolvable.
    # This is a "diagnostic contract" for future refactors; do not hard-fail the pipeline here.
    if not args.dry_run and episode and effective_wav_path and effective_srt_path.exists() and (run_dir / "image_cues.json").exists():
        try:
            manifest = build_timeline_manifest(
                run_dir=run_dir,
                episode=episode,
                audio_wav=effective_wav_path,
                audio_srt=effective_srt_path,
                image_cues_path=run_dir / "image_cues.json",
                belt_config_path=(run_dir / "belt_config.json") if (run_dir / "belt_config.json").exists() else None,
                capcut_draft_dir=(Path(args.draft_root) / draft_name),
                notes="auto_capcut_run (SoT=audio_tts_v2 final)",
                validate=True,
            )
        except Exception as e:
            log["timeline_manifest_error"] = str(e)
            manifest = build_timeline_manifest(
                run_dir=run_dir,
                episode=episode,
                audio_wav=effective_wav_path,
                audio_srt=effective_srt_path,
                image_cues_path=run_dir / "image_cues.json",
                belt_config_path=(run_dir / "belt_config.json") if (run_dir / "belt_config.json").exists() else None,
                capcut_draft_dir=(Path(args.draft_root) / draft_name),
                notes=f"auto_capcut_run (manifest validation failed: {e})",
                validate=False,
            )
        mf_path = write_timeline_manifest(run_dir, manifest)
        log["timeline_manifest"] = str(mf_path)

    log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")

    print("✅ Complete. Draft at:", log["draft"])
    print(f"   Images: {cue_count} | Duration: {total_duration/60:.1f} min | Labels: {labels}")
    if generated_title:
        print(f"   Title (LLM): {generated_title}")


if __name__ == "__main__":
    main()

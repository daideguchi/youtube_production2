#!/usr/bin/env python3
"""
Batch builder for CH02 CapCut drafts.

Modes:
  - images (default): run the full srt2images pipeline (cues+prompts+real images) then build the draft.
  - placeholder: bootstrap placeholder cues+noise images (no image API calls) then build the draft (debug-only).

This script ensures:
  - Final TTS wav/srt exist
    - If only .flac exists under workspaces/audio/final (space-saving), decode .flac -> .wav (lossless) for CapCut insertion.
    - If neither wav nor flac exist, generates via audio_tts with SKIP_TTS_READING=1.
  - Run dir exists with image_cues.json + images/
  - CapCut draft is created from CH02-テンプレ
  - Image source mix is applied per SSOT (flux-pro:flux-max:free=7:2:1)
  - Belt main text is patched from script status.json (SSOT)
  - Draft is validated (fail-fast)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional


try:
    from video_pipeline.tools._tool_bootstrap import bootstrap as tool_bootstrap
except Exception:
    from _tool_bootstrap import bootstrap as tool_bootstrap  # type: ignore

tool_bootstrap(load_env=False)

from factory_common.paths import (  # noqa: E402
    audio_artifacts_root,
    audio_pkg_root,
    repo_root,
    script_data_root,
    video_pkg_root,
    video_runs_root,
)

REPO_ROOT = repo_root()
PROJECT_ROOT = video_pkg_root()
TOOLS_DIR = PROJECT_ROOT / "tools"
RUN_ROOT = video_runs_root()
DEFAULT_CAPCUT_ROOT = Path.home() / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"

DEFAULT_SOURCE_MIX = {
    "weights": "7:2:1",  # flux-pro:flux-max:free
    "flux_pro_model_key": "f-3",
    "flux_max_model_key": "f-4",
    "broll_provider": "pexels",
    "broll_min_gap_sec": 60.0,
}


def _z3(x: str | int) -> str:
    return str(x).zfill(3)


def _derive_topic_from_status(channel: str, video: str) -> str:
    from factory_common.paths import status_path as script_status_path

    p = script_status_path(channel, video)
    data = json.loads(p.read_text(encoding="utf-8"))
    meta = data.get("metadata", {}) if isinstance(data, dict) else {}

    sheet_title = meta.get("sheet_title")
    if isinstance(sheet_title, str) and sheet_title.strip():
        m = re.search(r"【([^】]+)】", sheet_title)
        if m and (m.group(1) or "").strip():
            return (m.group(1) or "").strip()

    title = meta.get("title")
    if isinstance(title, str) and title.strip():
        if "燃え尽き" in title:
            return "静かな燃え尽き"
        if "優しさの疲労" in title:
            return "優しさの疲労"
        if "刃" in title and "丸め" in title:
            return "前向きの刃を丸める"
        if "刃" in title:
            return "言葉の刃"
        first = re.split(r"[。！？]", title.strip())[0]
        first = re.sub(r"\s+", "", first)
        return first[:14] if first else f"{channel}-{video}"

    return f"{channel}-{video}"


def _derive_capcut_title_from_status(channel: str, video: str) -> str:
    from factory_common.paths import status_path as script_status_path

    p = script_status_path(channel, video)
    data = json.loads(p.read_text(encoding="utf-8"))
    meta = data.get("metadata", {}) if isinstance(data, dict) else {}
    title = meta.get("title_sanitized") or meta.get("title") or ""
    if isinstance(title, str) and title.strip():
        return " ".join(title.split())
    return f"{channel}-{video}"


def _run(cmd: List[str], *, env: Optional[Dict[str, str]] = None, cwd: Optional[Path] = None) -> None:
    proc = subprocess.run(cmd, env=env, cwd=str(cwd) if cwd else None)
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


def _load_image_source_mix_from_sources(channel: str) -> Dict[str, object]:
    """
    Load CH02 image_source_mix config from SSOT `configs/sources.yaml`.

    Falls back to DEFAULT_SOURCE_MIX when:
      - file missing/unreadable
      - channel missing
      - image_source_mix missing/disabled
    """
    try:
        import yaml  # type: ignore

        src = (REPO_ROOT / "configs" / "sources.yaml").read_text(encoding="utf-8")
        data = yaml.safe_load(src) or {}
        ch = (data.get("channels") or {}).get(channel.upper()) or {}
        mix = ch.get("image_source_mix") or {}
        if not (mix.get("enabled") is True):
            return dict(DEFAULT_SOURCE_MIX)
        out = dict(DEFAULT_SOURCE_MIX)
        if str(mix.get("weights") or "").strip():
            out["weights"] = str(mix.get("weights") or "").strip()
        # Legacy field names kept in SSOT: gemini_model_key/schnell_model_key
        if str(mix.get("gemini_model_key") or "").strip():
            out["flux_pro_model_key"] = str(mix.get("gemini_model_key") or "").strip()
        if str(mix.get("schnell_model_key") or "").strip():
            out["flux_max_model_key"] = str(mix.get("schnell_model_key") or "").strip()
        if str(mix.get("broll_provider") or "").strip():
            out["broll_provider"] = str(mix.get("broll_provider") or "").strip()
        if mix.get("broll_min_gap_sec") is not None:
            out["broll_min_gap_sec"] = float(mix.get("broll_min_gap_sec") or 0.0)
        return out
    except Exception:
        return dict(DEFAULT_SOURCE_MIX)


def _decode_flac_to_wav(flac: Path, wav: Path) -> None:
    """
    Decode FLAC -> WAV (lossless) for tooling that requires WAV (CapCut/pyJianYingDraft).
    Keep sample rate/channels stable to avoid subtle drift.
    """
    wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(flac),
        "-acodec",
        "pcm_s16le",
        "-ar",
        "24000",
        "-ac",
        "1",
        str(wav),
    ]
    _run(cmd)


def ensure_tts_final(channel: str, video: str) -> tuple[Path, Path]:
    final_dir = audio_artifacts_root() / "final" / channel / video
    wav = final_dir / f"{channel}-{video}.wav"
    srt = final_dir / f"{channel}-{video}.srt"
    flac = final_dir / f"{channel}-{video}.flac"
    if wav.exists() and srt.exists():
        return wav, srt
    # Space-saving runs may keep only FLAC. Decode it back to WAV so downstream
    # CapCut tooling can insert voice audio deterministically.
    if (not wav.exists()) and flac.exists() and srt.exists():
        print(f"[AUDIO] Decoding FLAC -> WAV: {flac.name} -> {wav.name}")
        _decode_flac_to_wav(flac, wav)
        if wav.exists():
            return wav, srt

    assembled = script_data_root() / channel / video / "content" / "assembled_human.md"
    if not assembled.exists():
        assembled = script_data_root() / channel / video / "content" / "assembled.md"
    if not assembled.exists():
        raise SystemExit(f"Missing assembled.md: {assembled}")

    env = os.environ.copy()
    env["SKIP_TTS_READING"] = "1"
    env.setdefault("AOYAMA_SPEAKER_ID", "13")
    _run(
        [
            sys.executable,
            str(audio_pkg_root() / "scripts" / "run_tts.py"),
            "--channel",
            channel,
            "--video",
            video,
            "--input",
            str(assembled),
        ],
        env=env,
        cwd=REPO_ROOT,
    )

    if not wav.exists() or not srt.exists():
        raise SystemExit(f"TTS finished but final wav/srt missing under: {final_dir}")
    return wav, srt


def build_cues_only_run_dir(channel: str, run_name: str, srt: Path, *, imgdur: float) -> Path:
    """
    Create run_dir + image_cues.json without generating images (nanobanana=none).
    Requires LLM exec slot set to API to avoid THINK pending (see configs/llm_exec_slots.yaml).
    """
    run_dir = RUN_ROOT / run_name
    _run(
        [
            sys.executable,
            str(TOOLS_DIR / "run_pipeline.py"),
            "--srt",
            str(srt),
            "--out",
            str(run_dir),
            "--engine",
            "none",
            "--channel",
            channel,
            "--size",
            "1920x1080",
            "--fps",
            "30",
            "--imgdur",
            str(float(imgdur)),
            "--crossfade",
            "0.5",
            "--cue-mode",
            "grouped",
            "--nanobanana",
            "none",
            "--use-aspect-guide",
        ],
        cwd=PROJECT_ROOT,
    )
    return run_dir


def apply_image_source_mix(channel: str, run_dir: Path) -> None:
    cfg = _load_image_source_mix_from_sources(channel)
    _run(
        [
            sys.executable,
            str(TOOLS_DIR / "apply_image_source_mix.py"),
            str(run_dir),
            "--weights",
            str(cfg["weights"]),
            "--flux-pro-model-key",
            str(cfg["flux_pro_model_key"]),
            "--flux-max-model-key",
            str(cfg["flux_max_model_key"]),
            "--broll-provider",
            str(cfg["broll_provider"]),
            "--broll-min-gap-sec",
            str(cfg["broll_min_gap_sec"]),
            "--overwrite",
        ],
        cwd=REPO_ROOT,
    )


def regenerate_images_for_run(channel: str, run_dir: Path) -> None:
    _run(
        [
            sys.executable,
            str(TOOLS_DIR / "regenerate_images_from_cues.py"),
            "--run",
            str(run_dir),
            "--channel",
            channel,
            "--nanobanana",
            "direct",
            "--only-missing",
        ],
        cwd=REPO_ROOT,
    )


def bootstrap_run_dir(channel: str, video: str, run_name: str, srt: Path, *, imgdur: float) -> None:
    run_dir = RUN_ROOT / run_name
    main_title = _derive_topic_from_status(channel, video)
    _run(
        [
            sys.executable,
            str(TOOLS_DIR / "bootstrap_placeholder_run_dir.py"),
            "--srt",
            str(srt),
            "--out",
            str(run_dir),
            "--size",
            "1920x1080",
            "--fps",
            "30",
            "--imgdur",
            str(float(imgdur)),
            "--crossfade",
            "0.5",
            "--main-title",
            main_title,
        ],
        cwd=REPO_ROOT,
    )


def build_capcut_draft(
    channel: str,
    video: str,
    run_name: str,
    srt: Path,
    *,
    draft_root: Path,
    mode: str,
) -> None:
    title = _derive_capcut_title_from_status(channel, video)
    env = os.environ.copy()
    draft_root = draft_root.expanduser()
    env["YTM_CAPCUT_DRAFT_ROOT"] = str(draft_root)
    env["CAPCUT_DRAFT_ROOT"] = str(draft_root)
    cmd = [
        sys.executable,
        str(TOOLS_DIR / "auto_capcut_run.py"),
        "--channel",
        channel,
        "--srt",
        str(srt),
        "--run-name",
        run_name,
        "--title",
        title,
        "--no-draft-name-with-title",
        "--belt-mode",
        "existing",
        "--template",
        "CH02-テンプレ",
        "--draft-root",
        str(draft_root),
        # Keep draft folder naming stable for regen runs
        "--draft-name-policy",
        "run",
    ]

    # This script builds images separately (source-mix + regen-images), so draft build is always resume-only.
    # (placeholder is debug-only but also uses resume.)
    cmd += ["--resume", "--nanobanana", "none"]

    _run(
        cmd,
        env=env,
        cwd=PROJECT_ROOT,
    )

    _run(
        [
            sys.executable,
            str(TOOLS_DIR / "set_ch02_belt_from_status.py"),
            "--channel",
            channel,
            "--videos",
            video,
            "--update-run-belt-config",
        ],
        env=env,
        cwd=REPO_ROOT,
    )

    _run(
        [
            sys.executable,
            str(TOOLS_DIR / "validate_ch02_drafts.py"),
            "--channel",
            channel,
            "--videos",
            video,
            "--draft-root",
            str(draft_root.resolve()),
        ],
        env=env,
        cwd=REPO_ROOT,
    )


def _iter_videos(spec: str) -> List[str]:
    spec = spec.strip()
    if "-" in spec and "," not in spec:
        a, b = [x.strip() for x in spec.split("-", 1)]
        start, end = int(a), int(b)
        return [_z3(i) for i in range(start, end + 1)]
    return [_z3(x.strip()) for x in spec.split(",") if x.strip()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="CH02")
    ap.add_argument("--videos", default="034-080")
    ap.add_argument("--run-prefix", default="regen")
    ap.add_argument("--base-time", help="Base timestamp 'YYYYMMDD_HHMMSS' (optional)")
    ap.add_argument(
        "--imgdur",
        type=float,
        default=float(os.getenv("CH02_DRAFT_IMG_DUR_SEC", "25.0") or "25.0"),
        help="Target image duration seconds (default: 25.0; override via CH02_DRAFT_IMG_DUR_SEC)",
    )
    ap.add_argument(
        "--mode",
        choices=["images", "placeholder"],
        default="images",
        help="Build mode: images (real generated images) or placeholder (noise placeholders; debug-only)",
    )
    ap.add_argument(
        "--draft-root",
        type=Path,
        default=Path((os.getenv("YTM_CAPCUT_DRAFT_ROOT") or os.getenv("CAPCUT_DRAFT_ROOT")) or DEFAULT_CAPCUT_ROOT),
        help="CapCut draft root. NOTE: this process may not have macOS permission to write to ~/Movies; use a writable path if needed.",
    )
    args = ap.parse_args()

    channel = args.channel.upper()
    videos = _iter_videos(args.videos)
    if not videos:
        raise SystemExit("videos empty")

    if args.base_time:
        base = datetime.strptime(args.base_time, "%Y%m%d_%H%M%S")
    else:
        base = datetime.now().replace(microsecond=0)

    for idx, video in enumerate(videos):
        run_ts = (base + timedelta(seconds=idx)).strftime("%Y%m%d_%H%M%S")
        run_name = f"{channel}-{video}_{args.run_prefix}_{run_ts}"
        print(f"\n=== [{channel}-{video}] run_name={run_name} ===")
        wav, srt = ensure_tts_final(channel, video)
        print(f"[TTS] wav={wav} srt={srt}")
        if args.mode == "placeholder":
            bootstrap_run_dir(channel, video, run_name, srt, imgdur=float(args.imgdur))
        else:
            run_dir = build_cues_only_run_dir(channel, run_name, srt, imgdur=float(args.imgdur))
            apply_image_source_mix(channel, run_dir)
            regenerate_images_for_run(channel, run_dir)
        build_capcut_draft(channel, video, run_name, srt, draft_root=args.draft_root, mode=args.mode)

    print("\n[DONE] All requested videos completed.")


if __name__ == "__main__":
    main()

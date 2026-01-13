#!/usr/bin/env python3
"""
batch_regenerate_tts — UI BatchTTS 用のバックグラウンド実行スクリプト。

役割:
- 指定チャンネルの video を列挙し、`python -m script_pipeline.cli audio` を順番に実行
- 進捗 JSON とログを更新（UI がポーリングして表示）
- `--prepass` で「読み解決のみ（wav生成なし）」を高速に回せる（アノテーション除去/辞書適用の確認用）

安全:
- `script_pipeline.cli audio` 側に script_validation gate があるため、未検証台本は失敗として記録される
- 例外時も progress を error/complete に更新して “running のまま放置” を避ける
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple


from _bootstrap import bootstrap

PROJECT_ROOT = bootstrap()

from factory_common import paths as repo_paths


def _now_iso() -> str:
    return datetime.now().isoformat()


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_log(log_path: Path, text: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(text)
        if not text.endswith("\n"):
            f.write("\n")


def _discover_channels(data_root: Path) -> List[str]:
    channels: List[str] = []
    if not data_root.exists():
        return channels
    for ch_dir in sorted([p for p in data_root.iterdir() if p.is_dir()], key=lambda p: p.name):
        name = str(ch_dir.name).strip()
        if not name:
            continue
        # Heuristic: channel folders are typically "CHxx". We still require at least one numeric video dir.
        has_video = False
        for child in ch_dir.iterdir():
            if child.is_dir() and child.name.isdigit():
                has_video = True
                break
        if has_video:
            channels.append(name.upper())
    return channels


def _discover_targets(
    channels: List[str],
    *,
    min_video: int | None,
    max_video: int | None,
    only_missing_final: bool,
) -> Tuple[List[Tuple[str, str]], Dict[str, Dict[str, int]]]:
    data_root = repo_paths.script_data_root()
    targets: List[Tuple[str, str]] = []
    per_channel: Dict[str, Dict[str, int]] = {}

    for ch in channels:
        ch_dir = data_root / ch
        videos: List[str] = []
        if ch_dir.exists():
            for p in sorted(ch_dir.iterdir(), key=lambda x: x.name):
                if not p.is_dir():
                    continue
                if not p.name.isdigit():
                    continue
                n = int(p.name)
                if min_video is not None and n < min_video:
                    continue
                if max_video is not None and n > max_video:
                    continue
                videos.append(p.name.zfill(3))
        videos = sorted(set(videos), key=lambda s: int(s))
        if only_missing_final:
            videos = [v for v in videos if not repo_paths.audio_final_dir(ch, v).exists()]
        per_channel[ch] = {"total": len(videos), "completed": 0, "success": 0, "failed": 0}
        for v in videos:
            targets.append((ch, v))

    return targets, per_channel


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", action="append", help="Target channel (repeatable). e.g. CH06")
    ap.add_argument(
        "--all-channels",
        action="store_true",
        help="Auto-discover channels from workspaces/scripts and process all of them.",
    )
    ap.add_argument(
        "--exclude-channel",
        action="append",
        default=[],
        help="Exclude channel(s) (repeatable). Useful with --all-channels.",
    )
    ap.add_argument(
        "--progress-path",
        default=str(repo_paths.logs_root() / "ui" / "batch_tts_progress.json"),
        help="Path to batch_tts_progress.json (default: workspaces/logs/ui/batch_tts_progress.json)",
    )
    ap.add_argument(
        "--log-path",
        default=str(repo_paths.logs_root() / "ui" / "batch_tts_regeneration.log"),
        help="Path to batch_tts_regeneration.log (default: workspaces/logs/ui/batch_tts_regeneration.log)",
    )
    ap.add_argument("--resume", action="store_true", help="Resume from existing chunks (pass-through to CLI audio)")
    ap.add_argument(
        "--allow-unvalidated",
        action="store_true",
        help="Allow TTS even when script_validation is not completed (not recommended).",
    )
    ap.add_argument(
        "--force-overwrite-final",
        action="store_true",
        help="Force overwrite final artifacts (pass-through to CLI audio; required to regenerate finalize_existing episodes).",
    )
    ap.add_argument("--prepass", action="store_true", help="Reading-only pass (no wav synthesis).")
    ap.add_argument(
        "--skip-tts-reading",
        action="store_true",
        help="Disable reading-LLM path (sets SKIP_TTS_READING=1).",
    )
    ap.add_argument(
        "--only-missing-final",
        action="store_true",
        help="Only process videos missing workspaces/audio/final outputs.",
    )
    ap.add_argument("--min-video", type=int, default=None, help="Only process videos >= N (numeric).")
    ap.add_argument("--max-video", type=int, default=None, help="Only process videos <= N (numeric).")
    args = ap.parse_args()

    data_root = repo_paths.script_data_root()
    channels = [str(ch).strip().upper() for ch in (args.channel or []) if str(ch).strip()]
    if args.all_channels:
        channels.extend(_discover_channels(data_root))
    excluded = {str(ch).strip().upper() for ch in (args.exclude_channel or []) if str(ch).strip()}
    channels = sorted({ch for ch in channels if ch and ch not in excluded})
    if not channels:
        ap.error("No channels specified. Provide --channel CHxx (repeatable) or use --all-channels.")
    progress_path = Path(args.progress_path).expanduser().resolve()
    log_path = Path(args.log_path).expanduser().resolve()

    # Ensure monorepo imports work for subprocesses too.
    pythonpath = os.environ.get("PYTHONPATH", "")
    base_paths = [str(repo_paths.repo_root()), str(repo_paths.repo_root() / "packages")]
    if pythonpath:
        base_paths.append(pythonpath)
    os.environ["PYTHONPATH"] = os.pathsep.join(base_paths)

    targets, per_channel = _discover_targets(
        channels,
        min_video=args.min_video,
        max_video=args.max_video,
        only_missing_final=bool(args.only_missing_final),
    )
    progress: Dict[str, Any] = {
        "status": "running",
        "current_channel": None,
        "current_video": None,
        "completed": 0,
        "total": len(targets),
        "success": 0,
        "failed": 0,
        "current_step": "準備中...",
        "errors": [],
        "updated_at": _now_iso(),
        "channels": per_channel,
    }
    _write_json(progress_path, progress)

    _append_log(
        log_path,
        f"[batch_regenerate_tts] start {_now_iso()} channels={channels} excluded={sorted(excluded)} total={len(targets)} prepass={bool(args.prepass)} skip_tts_reading={bool(args.skip_tts_reading)} only_missing_final={bool(args.only_missing_final)}",
    )

    try:
        with log_path.open("a", encoding="utf-8") as log_fh:
            for ch, video in targets:
                progress["current_channel"] = ch
                progress["current_video"] = video
                progress["current_step"] = "audio_prepass" if args.prepass else "audio"
                progress["updated_at"] = _now_iso()
                _write_json(progress_path, progress)

                log_fh.write(f"\n=== [{_now_iso()}] START {ch}-{video} ===\n")
                log_fh.flush()

                env = os.environ.copy()
                if args.skip_tts_reading:
                    env["SKIP_TTS_READING"] = "1"
                # For prepass runs, retries mostly waste time (mismatch/fail-fast is deterministic).
                # Allow override by explicitly setting YTM_AUDIO_RETRY_COUNT in the parent environment.
                if args.prepass and "YTM_AUDIO_RETRY_COUNT" not in env:
                    env["YTM_AUDIO_RETRY_COUNT"] = "0"
                rc = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "script_pipeline.cli",
                        "audio",
                        "--channel",
                        ch,
                        "--video",
                        video,
                        *(["--resume"] if args.resume else []),
                        *(["--prepass"] if args.prepass else []),
                        *(["--allow-unvalidated"] if args.allow_unvalidated else []),
                        *(["--force-overwrite-final"] if args.force_overwrite_final else []),
                    ],
                    cwd=str(repo_paths.repo_root()),
                    env=env,
                    stdout=log_fh,
                    stderr=log_fh,
                    check=False,
                ).returncode

                rc = int(rc)
                log_fh.write(f"=== [{_now_iso()}] END {ch}-{video} exit={rc} ===\n")
                log_fh.flush()

                progress["completed"] += 1
                ch_state = progress.get("channels", {}).get(ch) or {}
                ch_state["completed"] = int(ch_state.get("completed", 0)) + 1
                if rc == 0:
                    progress["success"] += 1
                    ch_state["success"] = int(ch_state.get("success", 0)) + 1
                else:
                    progress["failed"] += 1
                    ch_state["failed"] = int(ch_state.get("failed", 0)) + 1
                    progress["errors"].append(
                        {
                            "channel": ch,
                            "video": video,
                            "error": f"{'audio_prepass' if args.prepass else 'audio'} failed (exit={rc}). see log.",
                        }
                    )
                progress["channels"][ch] = ch_state
                progress["updated_at"] = _now_iso()
                _write_json(progress_path, progress)

        progress["status"] = "completed"
        progress["current_channel"] = None
        progress["current_video"] = None
        progress["current_step"] = "完了"
        progress["updated_at"] = _now_iso()
        _write_json(progress_path, progress)
        _append_log(log_path, f"[batch_regenerate_tts] completed { _now_iso() } success={progress['success']} failed={progress['failed']}")
        return 0
    except Exception as exc:  # pragma: no cover - best effort
        tb = traceback.format_exc()
        _append_log(log_path, f"[batch_regenerate_tts] ERROR {type(exc).__name__}: {exc}\n{tb}")
        try:
            progress["status"] = "error"
            progress["current_step"] = f"error: {type(exc).__name__}"
            progress["updated_at"] = _now_iso()
            progress["errors"].append({"channel": progress.get("current_channel"), "video": progress.get("current_video"), "error": str(exc)})
            _write_json(progress_path, progress)
        except Exception:
            pass
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

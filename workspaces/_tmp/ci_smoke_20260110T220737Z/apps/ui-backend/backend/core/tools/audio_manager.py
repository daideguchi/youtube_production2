"""Audio synthesis helper used by the UI backend.

Historically this project used a thin CLI bridge (`packages/audio_tts/scripts/run_tts.py`).
The FastAPI backend expects `AudioManager.synthesize()` to exist and to raise on
failure, so endpoints can report errors instead of crashing with AttributeError.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from factory_common.paths import audio_final_dir, audio_pkg_root, repo_root, video_root


def _norm_channel(channel: str) -> str:
    return str(channel).upper()


def _norm_video(video: str) -> str:
    return str(video).zfill(3)


def _resolve_tts_input_path(channel: str, video: str) -> Path:
    base = video_root(channel, video)
    cand = base / "audio_prep" / "script_sanitized.txt"
    if cand.exists():
        return cand
    raise FileNotFoundError(
        f"script_sanitized.txt not found for regeneration (no fallback): {channel}-{video}"
    )


class AudioManager:
    def __init__(self, project_root: Optional[Path] = None, **_: Any):
        # project_root is treated as repo root by apps/ui-backend/backend/main.py
        self.project_root = Path(project_root).resolve() if project_root else repo_root()

    def synthesize(
        self,
        *,
        channel_code: str,
        video_number: str,
        engine_override: Optional[str] = None,
        reading_source: Optional[str] = None,
        voicepeak_narrator: Optional[str] = None,
        voicepeak_speed: Optional[int] = None,
        voicepeak_pitch: Optional[int] = None,
        voicepeak_emotion: Optional[str] = None,
    ) -> Dict[str, Any]:
        channel = _norm_channel(channel_code)
        video = _norm_video(video_number)
        input_path = _resolve_tts_input_path(channel, video)

        script = audio_pkg_root() / "scripts" / "run_tts.py"
        if not script.exists():
            raise FileNotFoundError(f"run_tts.py not found: {script}")

        env = os.environ.copy()
        # Ensure packages are importable when called from arbitrary cwd.
        env["PYTHONPATH"] = f"{repo_root()}:{repo_root() / 'packages'}"

        cmd = [
            sys.executable,
            str(script),
            "--channel",
            channel,
            "--video",
            video,
            "--input",
            str(input_path.resolve()),
        ]
        if engine_override:
            cmd.extend(["--engine-override", engine_override])
        if reading_source:
            cmd.extend(["--reading-source", reading_source])
        if voicepeak_narrator:
            cmd.extend(["--voicepeak-narrator", voicepeak_narrator])
        if voicepeak_speed is not None:
            cmd.extend(["--voicepeak-speed", str(int(voicepeak_speed))])
        if voicepeak_pitch is not None:
            cmd.extend(["--voicepeak-pitch", str(int(voicepeak_pitch))])
        if voicepeak_emotion:
            cmd.extend(["--voicepeak-emotion", voicepeak_emotion])

        try:
            completed = subprocess.run(
                cmd,
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                env=env,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(exc.stderr or exc.stdout or str(exc)) from exc

        final_dir = audio_final_dir(channel, video)
        final_wav = final_dir / f"{channel}-{video}.wav"
        final_log = final_dir / "log.json"
        if not final_wav.exists():
            raise RuntimeError(f"audio_tts did not create wav: {final_wav}")
        out_srt = final_wav.with_suffix(".srt")
        return {
            "channel": channel,
            "video": video,
            "input": str(input_path),
            "wav": str(final_wav),
            "srt": str(out_srt) if out_srt.exists() else None,
            "log": str(final_log) if final_log.exists() else None,
            "stdout": (completed.stdout or "").strip(),
        }

    # Back-compat alias (older stub used `generate`)
    def generate(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return self.synthesize(*args, **kwargs)

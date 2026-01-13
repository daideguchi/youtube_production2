from __future__ import annotations

import contextlib
import json
import wave
from pathlib import Path
from typing import Optional

from fastapi import HTTPException

from backend.app.normalize import normalize_channel_code, normalize_video_number
from factory_common.paths import audio_final_dir
from factory_common.paths import repo_root as ssot_repo_root
from factory_common.paths import script_data_root as ssot_script_data_root

REPO_ROOT = ssot_repo_root()
# NOTE: PROJECT_ROOT is treated as repo-root throughout the UI backend (legacy alias).
PROJECT_ROOT = REPO_ROOT
DATA_ROOT = ssot_script_data_root()


def video_base_dir(channel_code: str, video_number: str) -> Path:
    return DATA_ROOT / channel_code / video_number


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Invalid JSON: {path}") from exc


def load_status(channel_code: str, video_number: str) -> dict:
    status_path = video_base_dir(channel_code, video_number) / "status.json"
    if not status_path.exists():
        raise HTTPException(status_code=404, detail="status.json not found")
    return _load_json(status_path)


def load_status_optional(channel_code: str, video_number: str) -> Optional[dict]:
    status_path = video_base_dir(channel_code, video_number) / "status.json"
    if not status_path.exists():
        return None
    return _load_json(status_path)


def resolve_text_file(path: Path) -> Optional[str]:
    """正規パスのみを読む。フォールバック禁止。"""
    if not path.exists() or not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def _detect_artifact_path(channel_code: str, video_number: str, extension: str) -> Path:
    base = audio_final_dir(channel_code, video_number)
    if extension == ".wav":
        for ext in (".wav", ".flac", ".mp3", ".m4a"):
            candidate = base / f"{channel_code}-{video_number}{ext}"
            if candidate.exists():
                return candidate
    return base / f"{channel_code}-{video_number}{extension}"


def resolve_audio_path(status: dict, base_dir: Path) -> Optional[Path]:
    channel = normalize_channel_code(status.get("channel") or base_dir.parent.name)
    video_no = normalize_video_number(str(status.get("video_number") or base_dir.name))
    metadata = status.get("metadata", {}) if isinstance(status, dict) else {}
    audio_meta = metadata.get("audio", {}) if isinstance(metadata, dict) else {}
    synth_meta = audio_meta.get("synthesis", {}) if isinstance(audio_meta, dict) else {}
    final_wav = synth_meta.get("final_wav") if isinstance(synth_meta, dict) else None
    if final_wav:
        candidate = Path(str(final_wav))
        if not candidate.is_absolute():
            candidate = (PROJECT_ROOT / candidate).resolve()
        if candidate.exists():
            return candidate.resolve()

    final_candidate = _detect_artifact_path(channel, video_no, ".wav")
    if final_candidate.exists():
        return final_candidate.resolve()

    legacy_candidate = base_dir / "audio_prep" / f"{channel}-{video_no}.wav"
    return legacy_candidate.resolve() if legacy_candidate.exists() else None


def resolve_log_path(status: dict, base_dir: Path) -> Optional[Path]:
    channel = normalize_channel_code(status.get("channel") or base_dir.parent.name)
    video_no = normalize_video_number(str(status.get("video_number") or base_dir.name))
    final_log = audio_final_dir(channel, video_no) / "log.json"
    if final_log.exists():
        return final_log.resolve()
    candidate = base_dir / "audio_prep" / "log.json"
    if candidate.exists():
        return candidate.resolve()
    candidate_nested = base_dir / "audio_prep" / f"{channel}-{video_no}.log.json"
    return candidate_nested.resolve() if candidate_nested.exists() else None


def resolve_srt_path(status: dict, base_dir: Path) -> Optional[Path]:
    channel = normalize_channel_code(status.get("channel") or base_dir.parent.name)
    video_no = normalize_video_number(str(status.get("video_number") or base_dir.name))
    metadata = status.get("metadata", {}) if isinstance(status, dict) else {}
    srt_meta = metadata.get("subtitles", {}) if isinstance(metadata, dict) else {}
    final_srt = srt_meta.get("final_srt") if isinstance(srt_meta, dict) else None
    if final_srt:
        candidate = Path(str(final_srt))
        if not candidate.is_absolute():
            candidate = (PROJECT_ROOT / candidate).resolve()
        if candidate.exists():
            return candidate.resolve()

    final_candidate = _detect_artifact_path(channel, video_no, ".srt")
    if final_candidate.exists():
        return final_candidate.resolve()

    legacy_candidate = base_dir / "audio_prep" / f"{channel}-{video_no}.srt"
    return legacy_candidate.resolve() if legacy_candidate.exists() else None


def get_audio_duration_seconds(path: Path) -> Optional[float]:
    if not path.exists():
        return None
    try:
        with contextlib.closing(wave.open(str(path), "rb")) as wav_file:
            frames = wav_file.getnframes()
            rate = wav_file.getframerate()
            if rate:
                return round(frames / float(rate), 3)
    except (wave.Error, OSError):  # wave.Error for invalid WAV, OSError for unreadable file
        return None
    return None

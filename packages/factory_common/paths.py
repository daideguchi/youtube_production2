from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Repo / workspace roots
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def repo_root(start: Optional[Path] = None) -> Path:
    """
    Resolve repository root by searching for pyproject.toml.
    Env override:
      - YTM_REPO_ROOT: absolute path to repo root
    """
    override = os.getenv("YTM_REPO_ROOT") or os.getenv("YTM_ROOT")
    if override:
        return Path(override).expanduser().resolve()

    if start is None:
        start = Path(__file__).resolve()
    cur = start if start.is_dir() else start.parent

    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate.resolve()

    # Fallback: best-effort current directory
    return cur.resolve()


@lru_cache(maxsize=1)
def workspace_root() -> Path:
    """
    Root for SoT/workspaces.
    Env override:
      - YTM_WORKSPACE_ROOT
    """
    override = os.getenv("YTM_WORKSPACE_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return repo_root() / "workspaces"


def _prefer_new(old: Path, new: Path) -> Path:
    return new if new.exists() else old


def _norm_channel(ch: str) -> str:
    return str(ch).upper()


def _norm_video(video: str) -> str:
    v = str(video).zfill(3)
    return v


# ---------------------------------------------------------------------------
# Planning (progress) roots
# ---------------------------------------------------------------------------


def planning_root() -> Path:
    ws = workspace_root() / "planning"
    old = repo_root() / "progress"
    return _prefer_new(old, ws)


def channels_csv_path(channel: str) -> Path:
    ch = _norm_channel(channel)
    return planning_root() / "channels" / f"{ch}.csv"


def persona_path(channel: str) -> Path:
    ch = _norm_channel(channel)
    return planning_root() / "personas" / f"{ch}_PERSONA.md"


def research_root() -> Path:
    ws = workspace_root() / "research"
    old = repo_root() / "00_research"
    return _prefer_new(old, ws)


# ---------------------------------------------------------------------------
# Script pipeline roots
# ---------------------------------------------------------------------------


def script_pkg_root() -> Path:
    root = repo_root()
    new = root / "packages" / "script_pipeline"
    old = root / "script_pipeline"
    return _prefer_new(old, new)


def script_data_root() -> Path:
    ws = workspace_root() / "scripts"
    old = script_pkg_root() / "data"
    return _prefer_new(old, ws)


def video_root(channel: str, video: str) -> Path:
    ch = _norm_channel(channel)
    no = _norm_video(video)
    return script_data_root() / ch / no


def status_path(channel: str, video: str) -> Path:
    return video_root(channel, video) / "status.json"


# ---------------------------------------------------------------------------
# Audio / TTS roots
# ---------------------------------------------------------------------------


def audio_pkg_root() -> Path:
    root = repo_root()
    new = root / "packages" / "audio_tts_v2"
    old = root / "audio_tts_v2"
    return _prefer_new(old, new)


def audio_artifacts_root() -> Path:
    ws = workspace_root() / "audio"
    old = audio_pkg_root() / "artifacts"
    return _prefer_new(old, ws)


def audio_final_dir(channel: str, video: str) -> Path:
    ch = _norm_channel(channel)
    no = _norm_video(video)
    return audio_artifacts_root() / "final" / ch / no


# ---------------------------------------------------------------------------
# Video (CapCut / images) roots
# ---------------------------------------------------------------------------


def video_pkg_root() -> Path:
    root = repo_root()
    new = root / "packages" / "commentary_02_srt2images_timeline"
    old = root / "commentary_02_srt2images_timeline"
    return _prefer_new(old, new)


def video_runs_root() -> Path:
    ws = workspace_root() / "video" / "runs"
    old = video_pkg_root() / "output"
    return _prefer_new(old, ws)


def video_run_dir(run_id: str) -> Path:
    return video_runs_root() / str(run_id)


def video_input_root() -> Path:
    ws = workspace_root() / "video" / "input"
    old = video_pkg_root() / "input"
    return _prefer_new(old, ws)


# ---------------------------------------------------------------------------
# Thumbnails roots
# ---------------------------------------------------------------------------


def thumbnails_root() -> Path:
    ws = workspace_root() / "thumbnails"
    old = repo_root() / "thumbnails"
    return _prefer_new(old, ws)


def thumbnail_assets_dir(channel: str, video: str) -> Path:
    ch = _norm_channel(channel)
    no = _norm_video(video)
    return thumbnails_root() / "assets" / ch / no


# ---------------------------------------------------------------------------
# Logs roots
# ---------------------------------------------------------------------------


def logs_root() -> Path:
    ws = workspace_root() / "logs"
    old = repo_root() / "logs"
    return _prefer_new(old, ws)


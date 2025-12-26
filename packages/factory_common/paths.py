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


def offload_root() -> Optional[Path]:
    """
    Optional external storage root for offloaded artifacts (e.g., external SSD).

    Env override:
      - YTM_OFFLOAD_ROOT
      - FACTORY_OFFLOAD_ROOT (compat)
    """
    override = os.getenv("YTM_OFFLOAD_ROOT") or os.getenv("FACTORY_OFFLOAD_ROOT")
    if not override:
        return None
    return Path(override).expanduser().resolve()

def _norm_channel(ch: str) -> str:
    return str(ch).upper()


def _norm_video(video: str) -> str:
    v = str(video).zfill(3)
    return v


# ---------------------------------------------------------------------------
# Planning roots
# ---------------------------------------------------------------------------


def planning_root() -> Path:
    return workspace_root() / "planning"


def channels_csv_path(channel: str) -> Path:
    ch = _norm_channel(channel)
    return planning_root() / "channels" / f"{ch}.csv"


def persona_path(channel: str) -> Path:
    ch = _norm_channel(channel)
    return planning_root() / "personas" / f"{ch}_PERSONA.md"


def research_root() -> Path:
    return workspace_root() / "research"


# ---------------------------------------------------------------------------
# Script pipeline roots
# ---------------------------------------------------------------------------


def script_pkg_root() -> Path:
    return repo_root() / "packages" / "script_pipeline"


def script_data_root() -> Path:
    return workspace_root() / "scripts"


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
    return repo_root() / "packages" / "audio_tts"


def audio_artifacts_root() -> Path:
    return workspace_root() / "audio"


def audio_final_dir(channel: str, video: str) -> Path:
    ch = _norm_channel(channel)
    no = _norm_video(video)
    return audio_artifacts_root() / "final" / ch / no


# ---------------------------------------------------------------------------
# Video (CapCut / images) roots
# ---------------------------------------------------------------------------


def video_pkg_root() -> Path:
    return repo_root() / "packages" / "video_pipeline"


def video_runs_root() -> Path:
    return workspace_root() / "video" / "runs"


def video_run_dir(run_id: str) -> Path:
    return video_runs_root() / str(run_id)


def video_input_root() -> Path:
    return workspace_root() / "video" / "input"


def video_capcut_local_drafts_root() -> Path:
    """
    Local writable CapCut draft root (fallback when the real CapCut root is not writable).

    Canonical location:
      - workspaces/video/_capcut_drafts
    """
    return workspace_root() / "video" / "_capcut_drafts"


def video_state_root() -> Path:
    """
    Root for stateful, machine-generated video pipeline metadata.
    Env override:
      - YTM_VIDEO_STATE_ROOT
    """
    override = os.getenv("YTM_VIDEO_STATE_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return workspace_root() / "video" / "_state"


def video_audio_sync_status_path() -> Path:
    """
    Manifest for audio->video input sync status (checked flags, hashes, etc).

    Canonical location:
      - workspaces/video/_state/audio_sync_status.json
    """
    return video_state_root() / "audio_sync_status.json"


# ---------------------------------------------------------------------------
# Thumbnails roots
# ---------------------------------------------------------------------------


def thumbnails_root() -> Path:
    return workspace_root() / "thumbnails"


def thumbnail_assets_dir(channel: str, video: str) -> Path:
    ch = _norm_channel(channel)
    no = _norm_video(video)
    return thumbnails_root() / "assets" / ch / no


# ---------------------------------------------------------------------------
# Static assets (repo tracked)
# ---------------------------------------------------------------------------


def assets_root() -> Path:
    """
    Root for repo-tracked static assets (BGM/logo/overlays/etc).

    This is NOT a workspace-generated artifact; it is treated as L0/SoT and is
    intentionally tracked in git.
    """
    return repo_root() / "asset"


# ---------------------------------------------------------------------------
# Logs roots
# ---------------------------------------------------------------------------


def logs_root() -> Path:
    return workspace_root() / "logs"

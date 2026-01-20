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


def shared_storage_root() -> Optional[Path]:
    """
    Optional shared storage root (e.g., Tailscale-mounted always-on storage).

    Env override:
      - YTM_SHARED_STORAGE_ROOT
    """
    override = os.getenv("YTM_SHARED_STORAGE_ROOT")
    if not override:
        return None
    return Path(override).expanduser().resolve()


def shared_storage_namespace() -> str:
    """
    Shared storage namespace (repo identifier).

    Env override:
      - YTM_SHARED_STORAGE_NAMESPACE

    Default:
      - repo_root().name
    """
    override = str(os.getenv("YTM_SHARED_STORAGE_NAMESPACE") or "").strip()
    return override or repo_root().name


def shared_storage_base() -> Optional[Path]:
    """
    Base directory under shared storage for this repo's artifacts.
    Returns None if shared storage is not configured.
    """
    root = shared_storage_root()
    if root is None:
        return None
    return root / shared_storage_namespace()

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


def planning_channels_dir() -> Path:
    return planning_root() / "channels"


def channels_csv_path(channel: str) -> Path:
    ch = _norm_channel(channel)
    return planning_channels_dir() / f"{ch}.csv"


def planning_patches_root() -> Path:
    return planning_root() / "patches"


def persona_path(channel: str) -> Path:
    ch = _norm_channel(channel)
    return planning_root() / "personas" / f"{ch}_PERSONA.md"


def research_root() -> Path:
    return workspace_root() / "research"


# ---------------------------------------------------------------------------
# Idea cards (pre-planning inventory)
# ---------------------------------------------------------------------------


def ideas_root() -> Path:
    return planning_root() / "ideas"


def ideas_store_path(channel: str) -> Path:
    ch = _norm_channel(channel)
    return ideas_root() / f"{ch}.jsonl"


def ideas_archive_root() -> Path:
    return ideas_root() / "_archive"


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


def video_assets_root() -> Path:
    """
    Root for git-tracked video-related assets.

    Canonical location:
      - workspaces/video/assets
    """
    return workspace_root() / "video" / "assets"


def video_episode_assets_dir(channel: str, video: str) -> Path:
    """
    Editor-agnostic episode asset pack directory (git-tracked).

    Canonical location:
      - workspaces/video/assets/episodes/{CHxx}/{NNN}/
    """
    ch = _norm_channel(channel)
    no = _norm_video(video)
    return video_assets_root() / "episodes" / ch / no


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


# ---------------------------------------------------------------------------
# Secrets (operator local, untracked)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def secrets_root() -> Path:
    """
    Root for operator-local secrets/config that must NOT be stored in the repo.

    Env override:
      - YTM_SECRETS_ROOT
      - FACTORY_SECRETS_ROOT (compat)

    Default:
      - ~/.ytm/secrets
    """
    override = os.getenv("YTM_SECRETS_ROOT") or os.getenv("FACTORY_SECRETS_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".ytm" / "secrets"

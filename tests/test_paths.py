from __future__ import annotations

from pathlib import Path

import pytest

from factory_common import paths


def _clear_caches():
    paths.repo_root.cache_clear()
    paths.workspace_root.cache_clear()


def test_repo_root_detects_pyproject():
    _clear_caches()
    root = paths.repo_root()
    assert (root / "pyproject.toml").exists()


def test_repo_root_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("YTM_REPO_ROOT", str(tmp_path))
    _clear_caches()
    assert paths.repo_root() == tmp_path.resolve()


def test_default_roots_point_to_current_layout():
    _clear_caches()
    root = paths.repo_root()

    planning_new = root / "workspaces" / "planning"
    assert paths.planning_root() == (planning_new if planning_new.exists() else root / "progress")

    script_pkg_new = root / "packages" / "script_pipeline"
    assert paths.script_pkg_root() == (script_pkg_new if script_pkg_new.exists() else root / "script_pipeline")

    scripts_data_new = root / "workspaces" / "scripts"
    assert paths.script_data_root() == (
        scripts_data_new if scripts_data_new.exists() else paths.script_pkg_root() / "data"
    )

    audio_pkg_new = root / "packages" / "audio_tts_v2"
    assert paths.audio_pkg_root() == (audio_pkg_new if audio_pkg_new.exists() else root / "audio_tts_v2")

    audio_artifacts_new = root / "workspaces" / "audio"
    assert paths.audio_artifacts_root() == (
        audio_artifacts_new if audio_artifacts_new.exists() else paths.audio_pkg_root() / "artifacts"
    )

    video_pkg_new = root / "packages" / "commentary_02_srt2images_timeline"
    assert paths.video_pkg_root() == (
        video_pkg_new if video_pkg_new.exists() else root / "commentary_02_srt2images_timeline"
    )

    video_runs_new = root / "workspaces" / "video" / "runs"
    assert paths.video_runs_root() == (
        video_runs_new if video_runs_new.exists() else paths.video_pkg_root() / "output"
    )

    thumbnails_new = root / "workspaces" / "thumbnails"
    assert paths.thumbnails_root() == (thumbnails_new if thumbnails_new.exists() else root / "thumbnails")

    logs_new = root / "workspaces" / "logs"
    assert paths.logs_root() == (logs_new if logs_new.exists() else root / "logs")

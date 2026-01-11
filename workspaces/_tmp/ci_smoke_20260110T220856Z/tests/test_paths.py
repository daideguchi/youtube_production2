from __future__ import annotations

from pathlib import Path

import pytest

from factory_common import paths
from factory_common.repo_layout import unexpected_repo_root_entries


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
    assert paths.planning_root() == planning_new

    script_pkg_new = root / "packages" / "script_pipeline"
    assert paths.script_pkg_root() == script_pkg_new

    scripts_data_new = root / "workspaces" / "scripts"
    assert paths.script_data_root() == scripts_data_new

    audio_pkg_new = root / "packages" / "audio_tts"
    assert paths.audio_pkg_root() == audio_pkg_new

    audio_artifacts_new = root / "workspaces" / "audio"
    assert paths.audio_artifacts_root() == audio_artifacts_new

    video_pkg_new = root / "packages" / "video_pipeline"
    assert paths.video_pkg_root() == video_pkg_new

    video_runs_new = root / "workspaces" / "video" / "runs"
    assert paths.video_runs_root() == video_runs_new

    thumbnails_new = root / "workspaces" / "thumbnails"
    assert paths.thumbnails_root() == thumbnails_new

    logs_new = root / "workspaces" / "logs"
    assert paths.logs_root() == logs_new


def test_repo_root_has_no_unexpected_dirs_or_symlinks():
    _clear_caches()
    root = paths.repo_root()
    unexpected = unexpected_repo_root_entries(root)
    assert not unexpected, f"Unexpected repo-root dirs/symlinks: {[p.name for p in unexpected]}"

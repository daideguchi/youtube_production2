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

    assert paths.planning_root() == root / "progress"
    assert paths.script_pkg_root() == root / "script_pipeline"
    assert paths.script_data_root() == root / "script_pipeline" / "data"
    assert paths.audio_pkg_root() == root / "audio_tts_v2"
    assert paths.audio_artifacts_root() == root / "audio_tts_v2" / "artifacts"
    assert paths.video_pkg_root() == root / "commentary_02_srt2images_timeline"
    assert paths.video_runs_root() == root / "commentary_02_srt2images_timeline" / "output"
    assert paths.thumbnails_root() == root / "thumbnails"
    assert paths.logs_root() == root / "logs"


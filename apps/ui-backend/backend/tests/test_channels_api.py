from __future__ import annotations

from typing import Dict

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.app import channel_catalog
from backend.app import channel_info_store
from backend.app import episode_store
from backend.main import app


@pytest.fixture()
def channels_test_env(tmp_path, monkeypatch) -> Dict[str, object]:
    project_root = tmp_path
    workspace_root = tmp_path / "workspaces"
    scripts_root = workspace_root / "scripts"
    planning_channels_dir = workspace_root / "planning" / "channels"
    scripts_root.mkdir(parents=True, exist_ok=True)
    planning_channels_dir.mkdir(parents=True, exist_ok=True)

    # Planning SoT channels exist even when workspaces/scripts/CHxx is missing.
    (planning_channels_dir / "CH01.csv").write_text("チャンネル,No.,動画番号\n", encoding="utf-8")
    (planning_channels_dir / "CH02.csv").write_text("チャンネル,No.,動画番号\n", encoding="utf-8")

    # Only CH02 has a scripts directory (CH01 intentionally absent).
    (scripts_root / "CH02").mkdir(parents=True, exist_ok=True)

    # Patch globals to isolate from the real repo layout.
    script_pipeline_root = tmp_path / "script_pipeline"
    channels_dir = script_pipeline_root / "channels"
    channels_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(main, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(main, "DATA_ROOT", scripts_root)
    monkeypatch.setattr(main, "CHANNEL_PLANNING_DIR", planning_channels_dir)
    monkeypatch.setattr(main, "SCRIPT_PIPELINE_ROOT", script_pipeline_root)
    monkeypatch.setattr(channel_catalog, "DATA_ROOT", scripts_root)
    monkeypatch.setattr(channel_catalog, "CHANNEL_PLANNING_DIR", planning_channels_dir)
    monkeypatch.setattr(channel_catalog, "CHANNELS_DIR", channels_dir)
    monkeypatch.setattr(episode_store, "DATA_ROOT", scripts_root)
    monkeypatch.setattr(episode_store, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(channel_info_store, "CHANNELS_DIR", channels_dir)
    monkeypatch.setattr(channel_info_store, "CHANNEL_INFO_PATH", channels_dir / "channels_info.json")
    monkeypatch.setattr(channel_info_store, "CHANNEL_INFO", {})
    monkeypatch.setattr(channel_info_store, "CHANNEL_INFO_MTIME", 0.0)
    monkeypatch.setattr(main, "CHANNELS_DIR", channels_dir)
    monkeypatch.setattr(main, "CHANNEL_INFO_PATH", channels_dir / "channels_info.json")
    monkeypatch.setattr(main, "YOUTUBE_CLIENT", None)

    with TestClient(app) as client:
        yield {"client": client}


def test_list_channels_includes_planning_channels(channels_test_env):
    client: TestClient = channels_test_env["client"]  # type: ignore[assignment]
    response = client.get("/api/channels")
    assert response.status_code == 200
    codes = [item["code"] for item in response.json()]

    # CH01 should appear even without workspaces/scripts/CH01/
    assert codes[:2] == ["CH01", "CH02"]

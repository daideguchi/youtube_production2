from __future__ import annotations

import json
from typing import Dict

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.app import channel_info_store
from backend.main import app
from backend.routers import redo as redo_router


@pytest.fixture()
def redo_test_env(tmp_path, monkeypatch) -> Dict[str, object]:
    project_root = tmp_path
    workspace_root = tmp_path / "workspaces"
    scripts_root = workspace_root / "scripts"
    planning_channels_dir = workspace_root / "planning" / "channels"
    scripts_root.mkdir(parents=True, exist_ok=True)
    planning_channels_dir.mkdir(parents=True, exist_ok=True)

    # Minimal Planning SoT with two planned videos.
    (planning_channels_dir / "CH01.csv").write_text(
        "チャンネル,No.,動画番号,進捗\n"
        "CH01,1,1,\n"
        "CH01,2,2,投稿済み\n",
        encoding="utf-8",
    )

    # status.json exists for 001 only; 002 is missing but should be excluded via published lock.
    status_dir = scripts_root / "CH01" / "001"
    status_dir.mkdir(parents=True, exist_ok=True)
    (status_dir / "status.json").write_text(
        json.dumps(
            {
                "channel": "CH01",
                "video_number": "001",
                "status": "pending",
                "metadata": {"redo_script": False, "redo_audio": True},
                "stages": {},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    # Patch globals to isolate from the real repo layout.
    script_pipeline_root = tmp_path / "script_pipeline"
    channels_dir = script_pipeline_root / "channels"
    channels_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(main, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(main, "DATA_ROOT", scripts_root)
    monkeypatch.setattr(main, "CHANNEL_PLANNING_DIR", planning_channels_dir)
    monkeypatch.setattr(redo_router, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(redo_router, "DATA_ROOT", scripts_root)
    monkeypatch.setattr(redo_router, "CHANNEL_PLANNING_DIR", planning_channels_dir)
    monkeypatch.setattr(main, "SCRIPT_PIPELINE_ROOT", script_pipeline_root)
    monkeypatch.setattr(channel_info_store, "CHANNELS_DIR", channels_dir)
    monkeypatch.setattr(channel_info_store, "CHANNEL_INFO_PATH", channels_dir / "channels_info.json")
    monkeypatch.setattr(channel_info_store, "CHANNEL_INFO", {})
    monkeypatch.setattr(channel_info_store, "CHANNEL_INFO_MTIME", 0.0)
    monkeypatch.setattr(main, "CHANNELS_DIR", channels_dir)
    monkeypatch.setattr(main, "CHANNEL_INFO_PATH", channels_dir / "channels_info.json")
    monkeypatch.setattr(main, "YOUTUBE_CLIENT", None)

    with TestClient(app) as client:
        yield {"client": client}


def test_redo_summary_uses_status_meta_and_published_lock(redo_test_env):
    client: TestClient = redo_test_env["client"]  # type: ignore[assignment]

    response = client.get("/api/redo/summary?channel=CH01")
    assert response.status_code == 200
    payload = response.json()
    assert payload == [{"channel": "CH01", "redo_script": 0, "redo_audio": 1, "redo_both": 0}]

from __future__ import annotations

from typing import Dict

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.main import app


@pytest.fixture()
def dashboard_test_env(tmp_path, monkeypatch) -> Dict[str, object]:
    project_root = tmp_path
    workspace_root = tmp_path / "workspaces"
    scripts_root = workspace_root / "scripts"
    planning_channels_dir = workspace_root / "planning" / "channels"
    scripts_root.mkdir(parents=True, exist_ok=True)
    planning_channels_dir.mkdir(parents=True, exist_ok=True)

    # Planning SoT channels exist even when workspaces/scripts/CHxx is missing.
    (planning_channels_dir / "CH01.csv").write_text(
        "チャンネル,No.,動画番号\n"
        "CH01,1,1\n"
        "CH01,2,2\n",
        encoding="utf-8",
    )
    (planning_channels_dir / "CH02.csv").write_text(
        "チャンネル,No.,動画番号\n"
        "CH02,1,10\n",
        encoding="utf-8",
    )

    # Patch globals to isolate from the real repo layout.
    script_pipeline_root = tmp_path / "script_pipeline"
    channels_dir = script_pipeline_root / "channels"
    channels_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(main, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(main, "DATA_ROOT", scripts_root)
    monkeypatch.setattr(main, "CHANNEL_PLANNING_DIR", planning_channels_dir)
    monkeypatch.setattr(main, "SCRIPT_PIPELINE_ROOT", script_pipeline_root)
    monkeypatch.setattr(main, "CHANNELS_DIR", channels_dir)
    monkeypatch.setattr(main, "CHANNEL_INFO_PATH", channels_dir / "channels_info.json")
    monkeypatch.setattr(main, "CHANNEL_INFO", {})
    monkeypatch.setattr(main, "CHANNEL_INFO_MTIME", 0.0)
    monkeypatch.setattr(main, "YOUTUBE_CLIENT", None)

    with TestClient(app) as client:
        yield {"client": client}


def test_dashboard_overview_includes_planning_channels(dashboard_test_env):
    client: TestClient = dashboard_test_env["client"]  # type: ignore[assignment]
    response = client.get("/api/dashboard/overview")
    assert response.status_code == 200
    payload = response.json()
    by_code = {item["code"]: item for item in payload["channels"]}

    # Channels should be visible even when there are no status.json entries yet.
    assert list(by_code.keys()) == ["CH01", "CH02"]
    assert by_code["CH01"]["total"] == 2
    assert by_code["CH02"]["total"] == 1

    # Missing status.json entries are treated as pending work so UI progress does not show 100% started.
    assert payload["stage_matrix"]["CH01"]["script_outline"]["pending"] == 2
    assert payload["stage_matrix"]["CH02"]["script_outline"]["pending"] == 1

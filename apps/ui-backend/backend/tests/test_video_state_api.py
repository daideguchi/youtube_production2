from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.main import app


def _write_status(path: Path, *, updated_at: str = "v1") -> None:
    payload = {
        "script_id": "CH01-001",
        "channel": "CH01",
        "status": "pending",
        "metadata": {},
        "stages": {},
        "updated_at": updated_at,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


@pytest.fixture()
def video_state_env(tmp_path, monkeypatch) -> Dict[str, object]:
    scripts_root = tmp_path / "workspaces" / "scripts"
    planning_channels_dir = tmp_path / "workspaces" / "planning" / "channels"
    planning_channels_dir.mkdir(parents=True, exist_ok=True)

    (scripts_root / "CH01" / "001").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(main, "DATA_ROOT", scripts_root)
    monkeypatch.setattr(main, "CHANNEL_PLANNING_DIR", planning_channels_dir)
    monkeypatch.setattr(main, "PROGRESS_STATUS_PATH", scripts_root / "_progress" / "processing_status.json")

    with TestClient(app) as client:
        yield {"client": client, "base_dir": scripts_root / "CH01" / "001"}


def test_update_status_sets_and_clears_completed_at(video_state_env):
    client: TestClient = video_state_env["client"]  # type: ignore[assignment]
    base_dir: Path = video_state_env["base_dir"]  # type: ignore[assignment]

    status_path = base_dir / "status.json"
    _write_status(status_path)

    resp = client.put("/api/channels/CH01/videos/1/status", json={"status": "completed"})
    assert resp.status_code == 200

    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["status"] == "completed"
    assert status.get("completed_at")

    resp2 = client.put("/api/channels/CH01/videos/1/status", json={"status": "pending"})
    assert resp2.status_code == 200

    status2 = json.loads(status_path.read_text(encoding="utf-8"))
    assert status2["status"] == "pending"
    assert "completed_at" not in status2


def test_update_stages_updates_entries(video_state_env):
    client: TestClient = video_state_env["client"]  # type: ignore[assignment]
    base_dir: Path = video_state_env["base_dir"]  # type: ignore[assignment]

    status_path = base_dir / "status.json"
    _write_status(status_path)

    resp = client.put(
        "/api/channels/CH01/videos/1/stages",
        json={"stages": {"script_validation": {"status": "completed"}, "audio": {"status": "pending"}}},
    )
    assert resp.status_code == 200

    status = json.loads(status_path.read_text(encoding="utf-8"))
    stages = status.get("stages") or {}
    assert stages["script_validation"]["status"] == "completed"
    assert stages["script_validation"]["updated_at"]
    assert stages["audio"]["status"] == "pending"
    assert stages["audio"]["updated_at"]


def test_update_ready_sets_and_clears_ready_for_audio_at(video_state_env):
    client: TestClient = video_state_env["client"]  # type: ignore[assignment]
    base_dir: Path = video_state_env["base_dir"]  # type: ignore[assignment]

    status_path = base_dir / "status.json"
    _write_status(status_path)

    resp = client.put("/api/channels/CH01/videos/1/ready", json={"ready": True})
    assert resp.status_code == 200
    status = json.loads(status_path.read_text(encoding="utf-8"))
    meta = status.get("metadata") or {}
    assert meta.get("ready_for_audio") is True
    assert meta.get("ready_for_audio_at")

    resp2 = client.put("/api/channels/CH01/videos/1/ready", json={"ready": False})
    assert resp2.status_code == 200
    status2 = json.loads(status_path.read_text(encoding="utf-8"))
    meta2 = status2.get("metadata") or {}
    assert meta2.get("ready_for_audio") is False
    assert "ready_for_audio_at" not in meta2


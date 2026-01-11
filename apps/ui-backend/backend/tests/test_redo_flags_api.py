from __future__ import annotations

import json
from typing import Dict

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.main import app
from backend.routers import redo_flags as redo_flags_router


@pytest.fixture()
def redo_flags_env(tmp_path, monkeypatch) -> Dict[str, object]:
    scripts_root = tmp_path / "workspaces" / "scripts"
    planning_channels_dir = tmp_path / "workspaces" / "planning" / "channels"
    planning_channels_dir.mkdir(parents=True, exist_ok=True)

    status_dir = scripts_root / "CH01" / "001"
    status_dir.mkdir(parents=True, exist_ok=True)
    status_path = status_dir / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "script_id": "CH01-001",
                "channel": "CH01",
                "video_number": "001",
                "status": "pending",
                "metadata": {},
                "stages": {},
                "updated_at": "2026-01-01T00:00:00Z",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(main, "DATA_ROOT", scripts_root)
    monkeypatch.setattr(main, "CHANNEL_PLANNING_DIR", planning_channels_dir)
    monkeypatch.setattr(main, "PROGRESS_STATUS_PATH", scripts_root / "_progress" / "processing_status.json")

    with TestClient(app) as client:
        yield {"client": client, "status_path": status_path}


def test_update_video_redo_updates_metadata(redo_flags_env, monkeypatch):
    client: TestClient = redo_flags_env["client"]  # type: ignore[assignment]
    status_path = redo_flags_env["status_path"]  # type: ignore[assignment]

    monkeypatch.setattr(redo_flags_router, "is_episode_published_locked", lambda *_args, **_kwargs: False)

    resp = client.patch(
        "/api/channels/CH01/videos/1/redo",
        json={"redo_script": True, "redo_audio": False, "redo_note": "note"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "ok"
    assert payload["redo_script"] is True
    assert payload["redo_audio"] is False
    assert payload["redo_note"] == "note"
    assert payload["updated_at"]

    saved = json.loads(status_path.read_text(encoding="utf-8"))
    meta = saved.get("metadata") or {}
    assert meta["redo_script"] is True
    assert meta["redo_audio"] is False
    assert meta["redo_note"] == "note"
    assert saved["updated_at"] == payload["updated_at"]


def test_update_video_redo_rejects_when_published_locked(redo_flags_env, monkeypatch):
    client: TestClient = redo_flags_env["client"]  # type: ignore[assignment]

    monkeypatch.setattr(redo_flags_router, "is_episode_published_locked", lambda *_args, **_kwargs: True)

    resp = client.patch(
        "/api/channels/CH01/videos/1/redo",
        json={"redo_script": True},
    )
    assert resp.status_code == 423


from __future__ import annotations

import json
from typing import Dict

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.main import app


@pytest.fixture()
def thumbnail_override_env(tmp_path, monkeypatch) -> Dict[str, object]:
    scripts_root = tmp_path / "workspaces" / "scripts"
    planning_channels_dir = tmp_path / "workspaces" / "planning" / "channels"
    planning_channels_dir.mkdir(parents=True, exist_ok=True)

    channel_dir = scripts_root / "CH01" / "001"
    channel_dir.mkdir(parents=True, exist_ok=True)
    status_path = channel_dir / "status.json"
    status_path.write_text(
        json.dumps(
            {
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

    with TestClient(app) as client:
        yield {"client": client, "status_path": status_path}


def test_thumbnail_override_updates_status_metadata(thumbnail_override_env):
    client: TestClient = thumbnail_override_env["client"]  # type: ignore[assignment]
    status_path = thumbnail_override_env["status_path"]  # type: ignore[assignment]

    resp = client.patch(
        "/api/channels/CH01/videos/1/thumbnail",
        json={"thumbnail_url": "https://example.com/thumb.png", "thumbnail_path": "CH01/001/00_thumb.png"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "ok"
    assert payload["thumbnail_url"] == "https://example.com/thumb.png"
    assert payload["thumbnail_path"] == "CH01/001/00_thumb.png"
    assert payload["updated_at"]

    saved = json.loads(status_path.read_text(encoding="utf-8"))
    meta = saved.get("metadata") or {}
    assert meta["thumbnail_url_override"] == "https://example.com/thumb.png"
    assert meta["thumbnail_path_override"] == "CH01/001/00_thumb.png"
    assert saved["updated_at"] == payload["updated_at"]


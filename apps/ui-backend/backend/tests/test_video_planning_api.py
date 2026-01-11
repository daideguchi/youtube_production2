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
def video_planning_env(tmp_path, monkeypatch) -> Dict[str, object]:
    scripts_root = tmp_path / "workspaces" / "scripts"
    planning_channels_dir = tmp_path / "workspaces" / "planning" / "channels"
    planning_channels_dir.mkdir(parents=True, exist_ok=True)

    (scripts_root / "CH01" / "001").mkdir(parents=True, exist_ok=True)
    (planning_channels_dir / "CH01.csv").write_text("動画番号\n001\n", encoding="utf-8")

    monkeypatch.setattr(main, "DATA_ROOT", scripts_root)
    monkeypatch.setattr(main, "CHANNEL_PLANNING_DIR", planning_channels_dir)
    monkeypatch.setattr(main, "PROGRESS_STATUS_PATH", scripts_root / "_progress" / "processing_status.json")

    with TestClient(app) as client:
        yield {"client": client, "base_dir": scripts_root / "CH01" / "001"}


def test_update_video_planning_updates_status_json(video_planning_env):
    client: TestClient = video_planning_env["client"]  # type: ignore[assignment]
    base_dir: Path = video_planning_env["base_dir"]  # type: ignore[assignment]

    status_path = base_dir / "status.json"
    _write_status(status_path)

    resp = client.put(
        "/api/channels/CH01/videos/1/planning",
        json={"fields": {"primary_pain_tag": "人間関係"}, "creation_flag": "3"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["updated_at"]
    assert data["planning"]["creation_flag"] == "3"

    fields = {item["key"]: item["value"] for item in data["planning"]["fields"]}
    assert fields["primary_pain_tag"] == "人間関係"

    status = json.loads(status_path.read_text(encoding="utf-8"))
    meta = status.get("metadata") or {}
    assert (meta.get("planning") or {}).get("primary_pain_tag") == "人間関係"
    assert meta.get("sheet_flag") == "3"
    assert status.get("updated_at") == data["updated_at"]


def test_update_video_planning_noop_when_unchanged(video_planning_env):
    client: TestClient = video_planning_env["client"]  # type: ignore[assignment]
    base_dir: Path = video_planning_env["base_dir"]  # type: ignore[assignment]

    status_path = base_dir / "status.json"
    _write_status(status_path)

    first = client.put(
        "/api/channels/CH01/videos/1/planning",
        json={"fields": {"primary_pain_tag": "人間関係"}, "creation_flag": "3"},
    )
    assert first.status_code == 200
    updated_at = first.json()["updated_at"]

    second = client.put(
        "/api/channels/CH01/videos/1/planning",
        json={"fields": {"primary_pain_tag": "人間関係"}, "creation_flag": "3"},
    )
    assert second.status_code == 200
    data = second.json()
    assert data["status"] == "noop"
    assert data["updated_at"] == updated_at


def test_update_video_planning_conflict_on_stale_expected_updated_at(video_planning_env):
    client: TestClient = video_planning_env["client"]  # type: ignore[assignment]
    base_dir: Path = video_planning_env["base_dir"]  # type: ignore[assignment]

    status_path = base_dir / "status.json"
    _write_status(status_path, updated_at="v1")

    resp = client.put(
        "/api/channels/CH01/videos/1/planning",
        json={
            "fields": {"primary_pain_tag": "人間関係"},
            "expected_updated_at": "stale",
        },
    )
    assert resp.status_code == 409


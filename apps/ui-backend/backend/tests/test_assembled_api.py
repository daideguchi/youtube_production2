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
def assembled_env(tmp_path, monkeypatch) -> Dict[str, object]:
    scripts_root = tmp_path / "workspaces" / "scripts"
    planning_channels_dir = tmp_path / "workspaces" / "planning" / "channels"
    planning_channels_dir.mkdir(parents=True, exist_ok=True)

    (scripts_root / "CH01" / "001").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(main, "DATA_ROOT", scripts_root)
    monkeypatch.setattr(main, "CHANNEL_PLANNING_DIR", planning_channels_dir)
    monkeypatch.setattr(main, "PROGRESS_STATUS_PATH", scripts_root / "_progress" / "processing_status.json")

    with TestClient(app) as client:
        yield {"client": client, "base_dir": scripts_root / "CH01" / "001"}


def test_update_assembled_writes_assembled_md_when_no_human(assembled_env):
    client: TestClient = assembled_env["client"]  # type: ignore[assignment]
    base_dir: Path = assembled_env["base_dir"]  # type: ignore[assignment]

    _write_status(base_dir / "status.json")
    content_dir = base_dir / "content"
    content_dir.mkdir(parents=True, exist_ok=True)
    assembled = content_dir / "assembled.md"
    assembled.write_text("OLD", encoding="utf-8")

    resp = client.put(
        "/api/channels/CH01/videos/1/assembled",
        json={"content": "NEW"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "ok"
    assert payload["updated_at"]

    assert assembled.read_text(encoding="utf-8") == "NEW"
    assert not (content_dir / "assembled_human.md").exists()

    status = json.loads((base_dir / "status.json").read_text(encoding="utf-8"))
    meta = status.get("metadata") or {}
    assert meta.get("redo_script") is False
    assert meta.get("redo_audio") is True
    assert meta.get("audio_reviewed") is False

    sv = (status.get("stages") or {}).get("script_validation") or {}
    assert sv.get("status") == "pending"


def test_update_assembled_prefers_assembled_human_and_mirrors(assembled_env):
    client: TestClient = assembled_env["client"]  # type: ignore[assignment]
    base_dir: Path = assembled_env["base_dir"]  # type: ignore[assignment]

    _write_status(base_dir / "status.json")
    content_dir = base_dir / "content"
    content_dir.mkdir(parents=True, exist_ok=True)
    assembled = content_dir / "assembled.md"
    assembled_human = content_dir / "assembled_human.md"
    assembled.write_text("OLD_A", encoding="utf-8")
    assembled_human.write_text("OLD_H", encoding="utf-8")

    resp = client.put(
        "/api/channels/CH01/videos/1/assembled",
        json={"content": "NEW2"},
    )
    assert resp.status_code == 200
    assert assembled_human.read_text(encoding="utf-8") == "NEW2"
    assert assembled.read_text(encoding="utf-8") == "NEW2"


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
def tts_edit_env(tmp_path, monkeypatch) -> Dict[str, object]:
    scripts_root = tmp_path / "workspaces" / "scripts"
    planning_channels_dir = tmp_path / "workspaces" / "planning" / "channels"
    planning_channels_dir.mkdir(parents=True, exist_ok=True)

    # Make normalize_channel_code() accept CH01 via DATA_ROOT/CH01.
    (scripts_root / "CH01" / "001").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(main, "DATA_ROOT", scripts_root)
    monkeypatch.setattr(main, "CHANNEL_PLANNING_DIR", planning_channels_dir)
    monkeypatch.setattr(main, "PROGRESS_STATUS_PATH", scripts_root / "_progress" / "processing_status.json")

    with TestClient(app) as client:
        yield {"client": client, "base_dir": scripts_root / "CH01" / "001"}


def test_tts_validate_ok(tts_edit_env):
    client: TestClient = tts_edit_env["client"]  # type: ignore[assignment]

    resp = client.post(
        "/api/channels/CH01/videos/1/tts/validate",
        json={"content": "こんにちは"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["valid"] is True
    assert payload["issues"] == []
    assert "こんにちは" in payload["sanitized_content"]


def test_update_tts_writes_audio_prep_files(tts_edit_env):
    client: TestClient = tts_edit_env["client"]  # type: ignore[assignment]
    base_dir: Path = tts_edit_env["base_dir"]  # type: ignore[assignment]

    _write_status(base_dir / "status.json")

    resp = client.put(
        "/api/channels/CH01/videos/1/tts",
        json={"tagged_content": "私は猫です"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "ok"
    assert "updated_at" in payload
    assert payload["plain_content"] == "私は猫です"
    assert payload["tagged_content"] == "私は猫です"

    audio_prep_dir = base_dir / "audio_prep"
    assert (audio_prep_dir / "script_sanitized.txt").read_text(encoding="utf-8") == "私は猫です"
    assert (audio_prep_dir / "script_sanitized_with_pauses.txt").read_text(encoding="utf-8") == "私は猫です"


def test_replace_tts_segment_replaces_text(tts_edit_env):
    client: TestClient = tts_edit_env["client"]  # type: ignore[assignment]
    base_dir: Path = tts_edit_env["base_dir"]  # type: ignore[assignment]

    _write_status(base_dir / "status.json")

    audio_prep_dir = base_dir / "audio_prep"
    audio_prep_dir.mkdir(parents=True, exist_ok=True)
    (audio_prep_dir / "script_sanitized.txt").write_text("私は猫です", encoding="utf-8")

    resp = client.post(
        "/api/channels/CH01/videos/1/tts/replace",
        json={
            "original": "猫",
            "replacement": "犬",
            "scope": "first",
            "update_assembled": False,
            "regenerate_audio": False,
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["replaced"] == 1
    assert payload["audio_regenerated"] is False
    assert "犬" in payload["plain_content"]
    assert "猫" not in payload["plain_content"]

    assert (audio_prep_dir / "script_sanitized.txt").read_text(encoding="utf-8") == "私は犬です"


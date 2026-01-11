from __future__ import annotations

from pathlib import Path
from typing import Dict

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.main import app
from backend.routers import tts_text


@pytest.fixture()
def tts_plain_env(tmp_path, monkeypatch) -> Dict[str, object]:
    scripts_root = tmp_path / "workspaces" / "scripts"
    planning_channels_dir = tmp_path / "workspaces" / "planning" / "channels"
    planning_channels_dir.mkdir(parents=True, exist_ok=True)

    # Make normalize_channel_code() accept CH01 via DATA_ROOT/CH01.
    (scripts_root / "CH01").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(main, "DATA_ROOT", scripts_root)
    monkeypatch.setattr(main, "CHANNEL_PLANNING_DIR", planning_channels_dir)

    final_root = tmp_path / "workspaces" / "audio_artifacts" / "final"

    def _fake_audio_final_dir(channel_code: str, video_number: str) -> Path:
        return final_root / channel_code / video_number

    monkeypatch.setattr(tts_text, "audio_final_dir", _fake_audio_final_dir)

    with TestClient(app) as client:
        yield {"client": client, "scripts_root": scripts_root, "final_root": final_root}


def test_tts_plain_prefers_script_sanitized(tts_plain_env):
    client: TestClient = tts_plain_env["client"]  # type: ignore[assignment]
    scripts_root: Path = tts_plain_env["scripts_root"]  # type: ignore[assignment]
    final_root: Path = tts_plain_env["final_root"]  # type: ignore[assignment]

    base_dir = scripts_root / "CH01" / "001"
    audio_prep_dir = base_dir / "audio_prep"
    audio_prep_dir.mkdir(parents=True, exist_ok=True)
    (audio_prep_dir / "script_sanitized.txt").write_text("SANITIZED", encoding="utf-8")

    final_dir = final_root / "CH01" / "001"
    final_dir.mkdir(parents=True, exist_ok=True)
    (final_dir / "a_text.txt").write_text("FINAL", encoding="utf-8")

    resp = client.get("/api/channels/CH01/videos/1/tts/plain")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["content"] == "SANITIZED"
    assert payload["updated_at"]
    assert payload["path"].endswith("audio_prep/script_sanitized.txt")


def test_tts_plain_falls_back_to_final_snapshot(tts_plain_env):
    client: TestClient = tts_plain_env["client"]  # type: ignore[assignment]
    scripts_root: Path = tts_plain_env["scripts_root"]  # type: ignore[assignment]
    final_root: Path = tts_plain_env["final_root"]  # type: ignore[assignment]

    base_dir = scripts_root / "CH01" / "001"
    (base_dir / "audio_prep").mkdir(parents=True, exist_ok=True)

    final_dir = final_root / "CH01" / "001"
    final_dir.mkdir(parents=True, exist_ok=True)
    (final_dir / "a_text.txt").write_text("FINAL", encoding="utf-8")

    resp = client.get("/api/channels/CH01/videos/1/tts/plain")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["content"] == "FINAL"
    assert payload["updated_at"]
    assert payload["path"].endswith("a_text.txt")


def test_tts_plain_404_when_missing(tts_plain_env):
    client: TestClient = tts_plain_env["client"]  # type: ignore[assignment]
    scripts_root: Path = tts_plain_env["scripts_root"]  # type: ignore[assignment]

    base_dir = scripts_root / "CH01" / "001"
    (base_dir / "audio_prep").mkdir(parents=True, exist_ok=True)

    resp = client.get("/api/channels/CH01/videos/1/tts/plain")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "TTS input text not found (script_sanitized.txt / a_text.txt)"


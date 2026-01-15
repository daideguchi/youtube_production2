from __future__ import annotations

from pathlib import Path
from typing import Dict

import pytest
from fastapi.testclient import TestClient

from backend.app import normalize as normalize_mod
from backend import main
from backend.main import app
from backend.routers import audio_reports


@pytest.fixture()
def audio_reports_env(tmp_path, monkeypatch) -> Dict[str, object]:
    scripts_root = tmp_path / "workspaces" / "scripts"
    planning_channels_dir = tmp_path / "workspaces" / "planning" / "channels"
    planning_channels_dir.mkdir(parents=True, exist_ok=True)

    (scripts_root / "CH01" / "001").mkdir(parents=True, exist_ok=True)

    # Keep UI backend router normalization stable in tests (do not touch real repo).
    monkeypatch.setattr(main, "DATA_ROOT", scripts_root)
    monkeypatch.setattr(main, "CHANNEL_PLANNING_DIR", planning_channels_dir)

    monkeypatch.setattr(normalize_mod, "DATA_ROOT", scripts_root)
    monkeypatch.setattr(normalize_mod, "CHANNEL_PLANNING_DIR", planning_channels_dir)

    monkeypatch.setattr(audio_reports, "DATA_ROOT", scripts_root)

    final_root = tmp_path / "workspaces" / "audio_artifacts" / "final"

    def _fake_audio_final_dir(channel_code: str, video_number: str) -> Path:
        return final_root / channel_code / video_number

    monkeypatch.setattr(audio_reports, "audio_final_dir", _fake_audio_final_dir)

    with TestClient(app) as client:
        yield {"client": client, "scripts_root": scripts_root}


def test_audio_analysis_falls_back_to_a_text_preview(audio_reports_env):
    client: TestClient = audio_reports_env["client"]  # type: ignore[assignment]
    scripts_root: Path = audio_reports_env["scripts_root"]  # type: ignore[assignment]

    base_dir = scripts_root / "CH01" / "001"
    content_dir = base_dir / "content"
    content_dir.mkdir(parents=True, exist_ok=True)
    (content_dir / "assembled.md").write_text("A_TEXT", encoding="utf-8")

    resp = client.get("/api/audio/analysis/CH01/1")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["channel"] == "CH01"
    assert payload["video"] == "001"
    assert payload["b_text_with_pauses"] == "A_TEXT"


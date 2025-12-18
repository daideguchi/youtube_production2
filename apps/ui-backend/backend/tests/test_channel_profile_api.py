from __future__ import annotations

import json
from types import SimpleNamespace
from pathlib import Path
from typing import Dict

import pytest
from fastapi.testclient import TestClient

from ui.backend import main
from ui.backend.main import app
from script_pipeline.tools import planning_requirements


@pytest.fixture()
def channel_profile_test_env(tmp_path, monkeypatch) -> Dict[str, object]:
    project_root = tmp_path
    commentary_root = project_root / "script_pipeline"
    channels_dir = commentary_root / "channels"
    channel_dir = channels_dir / "CH01-TEST"
    channel_dir.mkdir(parents=True, exist_ok=True)
    info = {
        "channel_id": "CH01",
        "name": "テストチャンネル",
        "description": "テストの説明",
        "youtube": {
            "title": "公式タイトル",
            "description": "公式チャンネル説明",
            "handle": "@official",
        },
    }
    info_path = channel_dir / "channel_info.json"
    info_path.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")

    # Prepare audio config directory (empty)
    audio_dir = commentary_root / "audio" / "channels" / "CH01"
    audio_dir.mkdir(parents=True, exist_ok=True)
    (audio_dir / "voice_config.json").write_text(json.dumps({"voices": {"voicevox_a": {}}}), encoding="utf-8")

    # Patch globals
    monkeypatch.setattr(main, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(main, "COMMENTARY01_ROOT", commentary_root)
    monkeypatch.setattr(main, "CHANNELS_DIR", channels_dir)
    monkeypatch.setattr(main, "AUDIO_CHANNELS_DIR", commentary_root / "audio" / "channels")

    dummy_profile = SimpleNamespace(
        code="CH01",
        name="テストチャンネル",
        audience_profile="aud",
        persona_summary="persona",
        script_prompt="prompt",
        script_prompt_path=None,
    )
    monkeypatch.setattr(main, "load_channel_profile", lambda code: dummy_profile)

    # Planning requirements stubs
    monkeypatch.setattr(planning_requirements, "get_channel_persona", lambda code: "固定ペルソナ")
    monkeypatch.setattr(planning_requirements, "get_persona_doc_path", lambda code: "progress/personas/CH01_PERSONA.md")
    monkeypatch.setattr(planning_requirements, "get_channel_requirement_specs", lambda code: [])
    monkeypatch.setattr(planning_requirements, "get_description_defaults", lambda code: {})
    monkeypatch.setattr(
        planning_requirements,
        "get_planning_template_info",
        lambda code: {"path": "progress/templates/CH01_planning_template.csv", "headers": [], "sample": []},
    )

    with TestClient(app) as client:
        yield {
            "client": client,
            "info_path": info_path,
        }


def test_get_profile_prefers_custom_youtube_description(channel_profile_test_env):
    info_path: Path = channel_profile_test_env["info_path"]  # type: ignore[assignment]
    data = json.loads(info_path.read_text(encoding="utf-8"))
    data["youtube_description"] = "テンプレ説明"
    info_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    client: TestClient = channel_profile_test_env["client"]  # type: ignore[assignment]
    response = client.get("/api/channels/CH01/profile")
    assert response.status_code == 200
    payload = response.json()
    assert payload["youtube_description"] == "テンプレ説明"


def test_update_profile_persists_youtube_description(channel_profile_test_env):
    client: TestClient = channel_profile_test_env["client"]  # type: ignore[assignment]
    info_path: Path = channel_profile_test_env["info_path"]  # type: ignore[assignment]

    response = client.put(
        "/api/channels/CH01/profile",
        json={"youtube_description": "新しいテンプレ"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["youtube_description"] == "新しいテンプレ"

    persisted = json.loads(info_path.read_text(encoding="utf-8"))
    assert persisted.get("youtube_description") == "新しいテンプレ"

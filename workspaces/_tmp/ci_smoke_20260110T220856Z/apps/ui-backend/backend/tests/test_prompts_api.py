from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.app import prompts_store
from backend.main import app


@pytest.fixture()
def prompts_test_env(tmp_path, monkeypatch) -> Dict[str, object]:
    repo_root = tmp_path
    script_pipeline_root = tmp_path / "script_pipeline"
    prompts_root = script_pipeline_root / "prompts"
    templates_root = prompts_root / "templates"
    channels_root = script_pipeline_root / "channels"
    templates_root.mkdir(parents=True, exist_ok=True)
    channels_root.mkdir(parents=True, exist_ok=True)

    base_prompts = {
        "youtube_description_prompt.txt": "desc\n",
        "phase2_audio_prompt.txt": "phase2\n",
        "llm_polish_template.txt": "polish\n",
        "orchestrator_prompt.txt": "orchestrator\n",
        "chapter_enhancement_prompt.txt": "chapter\n",
        "init.txt": "init\n",
    }
    for name, content in base_prompts.items():
        (prompts_root / name).write_text(content, encoding="utf-8")

    (templates_root / "story.txt").write_text("story template\n", encoding="utf-8")
    (prompts_root / "extra_prompt.txt").write_text("extra prompt\n", encoding="utf-8")

    channel_dir = channels_root / "CH01-test"
    channel_dir.mkdir(parents=True, exist_ok=True)
    (channel_dir / "script_prompt.txt").write_text("channel prompt\n", encoding="utf-8")
    (channel_dir / "channel_info.json").write_text(
        json.dumps({"channel_id": "CH01", "script_prompt": "channel prompt"}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(prompts_store, "PROJECT_ROOT", repo_root)
    monkeypatch.setattr(prompts_store, "SCRIPT_PIPELINE_ROOT", script_pipeline_root)
    monkeypatch.setattr(prompts_store, "SCRIPT_PIPELINE_PROMPTS_ROOT", prompts_root)
    monkeypatch.setattr(prompts_store, "PROMPT_TEMPLATES_ROOT", templates_root)
    monkeypatch.setattr(prompts_store, "CHANNELS_DIR", channels_root)

    monkeypatch.setattr(main, "PROJECT_ROOT", repo_root)

    with TestClient(app) as client:
        yield {"client": client, "paths": {"repo_root": repo_root, "channel_info": channel_dir / "channel_info.json"}}


def test_list_prompts_includes_curated_and_discovered(prompts_test_env):
    client: TestClient = prompts_test_env["client"]  # type: ignore[assignment]

    response = client.get("/api/prompts")
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()}

    assert "youtube_description_prompt" in ids
    assert "template_story" in ids
    assert "script_pipeline_prompt_extra_prompt" in ids
    assert "channel_ch01_script_prompt" in ids


def test_fetch_prompt_document(prompts_test_env):
    client: TestClient = prompts_test_env["client"]  # type: ignore[assignment]

    response = client.get("/api/prompts/youtube_description_prompt")
    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "youtube_description_prompt"
    assert payload["content"] == "desc\n"
    assert payload["checksum"]


def test_update_prompt_document_detects_conflict(prompts_test_env):
    client: TestClient = prompts_test_env["client"]  # type: ignore[assignment]

    current = client.get("/api/prompts/youtube_description_prompt").json()
    response = client.put(
        "/api/prompts/youtube_description_prompt",
        json={"content": "new\n", "expected_checksum": "not-matching"},
    )
    assert response.status_code == 409
    assert client.get("/api/prompts/youtube_description_prompt").json()["checksum"] == current["checksum"]


def test_update_channel_prompt_updates_channel_info(prompts_test_env):
    client: TestClient = prompts_test_env["client"]  # type: ignore[assignment]
    channel_info: Path = prompts_test_env["paths"]["channel_info"]  # type: ignore[index]

    current = client.get("/api/prompts/channel_ch01_script_prompt").json()
    response = client.put(
        "/api/prompts/channel_ch01_script_prompt",
        json={"content": "updated channel prompt\n", "expected_checksum": current["checksum"]},
    )
    assert response.status_code == 200

    info = json.loads(channel_info.read_text(encoding="utf-8"))
    assert info["script_prompt"] == "updated channel prompt"

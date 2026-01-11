from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.main import app


@pytest.fixture()
def human_scripts_env(tmp_path, monkeypatch) -> Dict[str, object]:
    scripts_root = tmp_path / "workspaces" / "scripts"
    planning_channels_dir = tmp_path / "workspaces" / "planning" / "channels"
    planning_channels_dir.mkdir(parents=True, exist_ok=True)

    # Make normalize_channel_code() accept CH01 via DATA_ROOT/CH01.
    (scripts_root / "CH01").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(main, "DATA_ROOT", scripts_root)
    monkeypatch.setattr(main, "CHANNEL_PLANNING_DIR", planning_channels_dir)
    monkeypatch.setattr(main, "PROGRESS_STATUS_PATH", scripts_root / "_progress" / "processing_status.json")

    with TestClient(app) as client:
        yield {"client": client, "scripts_root": scripts_root}


def test_human_scripts_get_returns_contents_and_no_warnings(human_scripts_env):
    client: TestClient = human_scripts_env["client"]  # type: ignore[assignment]
    scripts_root: Path = human_scripts_env["scripts_root"]  # type: ignore[assignment]

    base_dir = scripts_root / "CH01" / "001"
    content_dir = base_dir / "content"
    audio_prep_dir = base_dir / "audio_prep"
    content_dir.mkdir(parents=True, exist_ok=True)
    audio_prep_dir.mkdir(parents=True, exist_ok=True)

    (base_dir / "status.json").write_text(
        json.dumps(
            {
                "channel": "CH01",
                "video_number": "001",
                "status": "pending",
                "metadata": {"audio_reviewed": True},
                "stages": {},
                "updated_at": "2026-01-01T00:00:00Z",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    (audio_prep_dir / "b_text_with_pauses.txt").write_text("B_WITH_PAUSES", encoding="utf-8")
    (audio_prep_dir / "script_sanitized.txt").write_text("TTS_PLAIN", encoding="utf-8")

    (content_dir / "assembled.md").write_text("A", encoding="utf-8")
    (content_dir / "assembled_human.md").write_text("AH", encoding="utf-8")
    (content_dir / "script_audio.txt").write_text("B", encoding="utf-8")
    (content_dir / "script_audio_human.txt").write_text("BH", encoding="utf-8")

    resp = client.get("/api/channels/CH01/videos/1/scripts/human")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["assembled_content"] == "A"
    assert payload["assembled_human_content"] == "AH"
    assert payload["script_audio_content"] == "B"
    assert payload["script_audio_human_content"] == "BH"
    assert payload["audio_reviewed"] is True
    assert payload["updated_at"] == "2026-01-01T00:00:00Z"
    assert payload["warnings"] == []
    assert payload["assembled_path"].endswith("content/assembled.md")
    assert payload["assembled_human_path"].endswith("content/assembled_human.md")
    assert payload["script_audio_path"].endswith("content/script_audio.txt")
    assert payload["script_audio_human_path"].endswith("content/script_audio_human.txt")


def test_human_scripts_get_falls_back_to_tts_plain_when_missing(human_scripts_env):
    client: TestClient = human_scripts_env["client"]  # type: ignore[assignment]
    scripts_root: Path = human_scripts_env["scripts_root"]  # type: ignore[assignment]

    base_dir = scripts_root / "CH01" / "001"
    audio_prep_dir = base_dir / "audio_prep"
    audio_prep_dir.mkdir(parents=True, exist_ok=True)
    (audio_prep_dir / "script_sanitized.txt").write_text("TTS_PLAIN", encoding="utf-8")

    resp = client.get("/api/channels/CH01/videos/1/scripts/human")
    assert resp.status_code == 200
    payload = resp.json()

    # assembled.md が無い場合は TTS plain を返す（互換維持）
    assert payload["assembled_content"] == "TTS_PLAIN"
    assert payload["assembled_human_content"] == "TTS_PLAIN"
    assert payload["script_audio_human_content"] == "TTS_PLAIN"
    assert "status.json missing for CH01-001" in payload["warnings"]
    assert "b_text_with_pauses.txt missing for CH01-001" in payload["warnings"]


def test_human_scripts_put_updates_a_text_and_resets_flags(human_scripts_env):
    client: TestClient = human_scripts_env["client"]  # type: ignore[assignment]
    scripts_root: Path = human_scripts_env["scripts_root"]  # type: ignore[assignment]

    base_dir = scripts_root / "CH01" / "001"
    content_dir = base_dir / "content"
    content_dir.mkdir(parents=True, exist_ok=True)

    status_path = base_dir / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "channel": "CH01",
                "video_number": "001",
                "status": "pending",
                "metadata": {"redo_script": True, "redo_audio": False, "audio_reviewed": True},
                "stages": {"script_validation": {"status": "completed", "details": {"error": "x", "issues": [1]}}},
                "updated_at": "2026-01-01T00:00:00Z",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    resp = client.put(
        "/api/channels/CH01/videos/1/scripts/human",
        json={
            "assembled_human": "AH2",
            "expected_updated_at": "2026-01-01T00:00:00Z",
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "ok"
    assert payload["updated_at"]
    assert payload["audio_reviewed"] is False

    # Files are mirrored (assembled_human is authoritative, assembled.md mirrors it).
    assert (content_dir / "assembled_human.md").read_text(encoding="utf-8") == "AH2"
    assert (content_dir / "assembled.md").read_text(encoding="utf-8") == "AH2"

    saved = json.loads(status_path.read_text(encoding="utf-8"))
    meta = saved.get("metadata") or {}
    assert meta["redo_script"] is False
    assert meta["redo_audio"] is True
    assert meta["audio_reviewed"] is False
    sv = (saved.get("stages") or {}).get("script_validation") or {}
    assert sv.get("status") == "pending"
    assert "error" not in (sv.get("details") or {})
    assert "issues" not in (sv.get("details") or {})


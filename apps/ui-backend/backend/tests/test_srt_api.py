from __future__ import annotations

import json
import wave
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


def _write_wav(path: Path, *, seconds: float = 1.0, rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)  # 16-bit
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * frames)


def _write_srt(path: Path, *, end_ms: int = 1000) -> None:
    # Minimal 1-block SRT that should match the generated WAV duration.
    seconds = end_ms / 1000.0
    end = f"00:00:{int(seconds):02d},{int(end_ms % 1000):03d}"
    content = f"1\n00:00:00,000 --> {end}\nテスト\n\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture()
def srt_env(tmp_path, monkeypatch) -> Dict[str, object]:
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


def test_update_srt_writes_file_and_updates_status(srt_env):
    client: TestClient = srt_env["client"]  # type: ignore[assignment]
    base_dir: Path = srt_env["base_dir"]  # type: ignore[assignment]

    _write_status(base_dir / "status.json")

    audio_prep_dir = base_dir / "audio_prep"
    audio_prep_dir.mkdir(parents=True, exist_ok=True)
    legacy_srt = audio_prep_dir / "CH01-001.srt"
    legacy_srt.write_text("OLD", encoding="utf-8")

    resp = client.put(
        "/api/channels/CH01/videos/1/srt",
        json={"content": "NEW"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert legacy_srt.read_text(encoding="utf-8") == "NEW"

    status = json.loads((base_dir / "status.json").read_text(encoding="utf-8"))
    synth = status.get("metadata", {}).get("audio", {}).get("synthesis", {})
    assert str(synth.get("final_srt") or "").endswith("CH01-001.srt")


def test_verify_srt_ok(srt_env):
    client: TestClient = srt_env["client"]  # type: ignore[assignment]
    base_dir: Path = srt_env["base_dir"]  # type: ignore[assignment]

    _write_status(base_dir / "status.json")

    audio_prep_dir = base_dir / "audio_prep"
    legacy_wav = audio_prep_dir / "CH01-001.wav"
    legacy_srt = audio_prep_dir / "CH01-001.srt"
    _write_wav(legacy_wav, seconds=1.0)
    _write_srt(legacy_srt, end_ms=1000)

    resp = client.post("/api/channels/CH01/videos/1/srt/verify")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["valid"] is True
    assert payload["issues"] == []


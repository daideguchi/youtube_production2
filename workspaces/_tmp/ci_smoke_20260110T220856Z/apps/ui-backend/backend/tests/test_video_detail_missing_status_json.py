from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from test_planning_api import planning_test_env


def _append_planning_row(path: Path, *, video_number: str, title: str) -> None:
    header = path.read_text(encoding="utf-8")
    if not header.endswith("\n"):
        header += "\n"
    row = f"CH01,001,{video_number},CH01-{video_number.zfill(3)},,{title},,\n"
    path.write_text(header + row, encoding="utf-8")


def test_video_detail_allows_missing_status_json(planning_test_env):
    client: TestClient = planning_test_env["client"]  # type: ignore[assignment]
    channels_dir: Path = planning_test_env["channels_dir"]  # type: ignore[assignment]
    scripts_root: Path = planning_test_env["scripts_root"]  # type: ignore[assignment]

    planning_csv = channels_dir / "CH01.csv"
    _append_planning_row(planning_csv, video_number="1", title="テストタイトル")

    base_dir = scripts_root / "CH01" / "001"
    (base_dir / "content").mkdir(parents=True, exist_ok=True)
    (base_dir / "content" / "assembled.md").write_text("これはテスト台本です。\n", encoding="utf-8")

    response = client.get("/api/channels/CH01/videos/001")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "pending"
    assert "テスト台本" in (data.get("assembled_content") or "")
    assert any("status.json missing" in warning for warning in data.get("warnings", []))

    response2 = client.get("/api/channels/CH01/videos/001/scripts/human")
    assert response2.status_code == 200
    data2 = response2.json()
    assert "テスト台本" in (data2.get("assembled_content") or "")
    assert any("status.json missing" in warning for warning in data2.get("warnings", []))


def test_update_human_scripts_bootstraps_status_json(planning_test_env):
    client: TestClient = planning_test_env["client"]  # type: ignore[assignment]
    channels_dir: Path = planning_test_env["channels_dir"]  # type: ignore[assignment]
    scripts_root: Path = planning_test_env["scripts_root"]  # type: ignore[assignment]

    planning_csv = channels_dir / "CH01.csv"
    _append_planning_row(planning_csv, video_number="1", title="テストタイトル")

    response = client.put(
        "/api/channels/CH01/videos/001/scripts/human",
        json={
            "assembled_human": "Aテキスト\n",
            "script_audio_human": "Bテキスト\n",
            "expected_updated_at": None,
        },
    )
    assert response.status_code == 200

    status_path = scripts_root / "CH01" / "001" / "status.json"
    assert status_path.exists()
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["script_id"] == "CH01-001"
    assert status["channel"] == "CH01"
    assert status["stages"]["script_validation"]["status"] == "pending"

    assembled_human_path = scripts_root / "CH01" / "001" / "content" / "assembled_human.md"
    assert assembled_human_path.read_text(encoding="utf-8") == "Aテキスト\n"
    mirror_path = scripts_root / "CH01" / "001" / "content" / "assembled.md"
    assert mirror_path.read_text(encoding="utf-8") == "Aテキスト\n"

    script_audio_human_path = scripts_root / "CH01" / "001" / "content" / "script_audio_human.txt"
    assert script_audio_human_path.read_text(encoding="utf-8") == "Bテキスト\n"
    b_with_pauses_path = scripts_root / "CH01" / "001" / "audio_prep" / "b_text_with_pauses.txt"
    assert not b_with_pauses_path.exists()

    response2 = client.get("/api/channels/CH01/videos/001")
    assert response2.status_code == 200
    data2 = response2.json()
    assert not any("status.json missing" in warning for warning in data2.get("warnings", []))

    response3 = client.get("/api/channels/CH01/videos/001/scripts/human")
    assert response3.status_code == 200
    data3 = response3.json()
    assert not any("status.json missing" in warning for warning in data3.get("warnings", []))

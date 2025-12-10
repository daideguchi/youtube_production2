from __future__ import annotations

from pathlib import Path
from typing import Dict, Tuple
import sys

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ui.backend import main
from ui.backend.main import app
from script_pipeline.tools import planning_requirements


def _read_planning_header() -> str:
    # fallback header for channel CSVs
    return "チャンネル,No.,動画番号,動画ID,台本番号,タイトル,進捗,作成フラグ"


@pytest.fixture()
def planning_test_env(tmp_path, monkeypatch) -> Dict[str, object]:
    progress_dir = tmp_path / "progress"
    channels_dir = progress_dir / "channels"
    channels_dir.mkdir(parents=True, exist_ok=True)
    planning_csv = channels_dir / "CH01.csv"
    planning_csv.write_text(_read_planning_header() + "\n", encoding="utf-8")

    persona_dir = tmp_path / "progress" / "personas"
    persona_dir.mkdir(parents=True, exist_ok=True)
    persona_text = "他人の目や言葉に振り回されがちな40〜60代。真面目で優しいがゆえに、人間関係・老後・お金・孤独の不安を抱え、仏教の整理術で心を軽くしたいと願っている人。"
    (persona_dir / "CH01_PERSONA.md").write_text(f"# Persona\n> {persona_text}\n", encoding="utf-8")

    templates_dir = tmp_path / "progress" / "templates"
    templates_dir.mkdir(parents=True, exist_ok=True)
    (templates_dir / "CH01_planning_template.csv").write_text(
        "チャンネル,No.,悩みタグ_メイン\nCH01,001,人間関係\n",
        encoding="utf-8"
    )

    monkeypatch.setattr(main, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(main, "PLANNING_CSV_PATH", None)
    monkeypatch.setattr(main, "CHANNEL_PLANNING_DIR", channels_dir)
    monkeypatch.setattr(planning_requirements, "SSOT_DIR", persona_dir)
    monkeypatch.setattr(planning_requirements, "YTM_ROOT", tmp_path)
    planning_requirements.clear_persona_cache()
    monkeypatch.setattr(main.planning_store, "refresh", lambda force=False: None)

    with TestClient(app) as client:
        yield {
            "client": client,
            "channels_dir": channels_dir,
            "persona_text": persona_text,
            "persona_text_path": str(persona_dir / "CH01_PERSONA.md"),
        }


def test_create_planning_entry_requires_required_fields(planning_test_env):
    client: TestClient = planning_test_env["client"]  # type: ignore[assignment]
    response = client.post(
        "/api/planning",
        json={
            "channel": "CH01",
            "video_number": "191",
            "title": "【TEST】missing tags",
            "fields": {},
        },
    )
    assert response.status_code == 400
    assert "必須フィールド" in response.json()["detail"]


def test_create_planning_entry_sets_persona_and_defaults(planning_test_env):
    client: TestClient = planning_test_env["client"]  # type: ignore[assignment]
    persona_text = planning_test_env["persona_text"]
    payload = {
        "channel": "CH01",
        "video_number": "192",
        "title": "【TEST】auto fields",
        "fields": {
            "primary_pain_tag": "人間関係",
            "secondary_pain_tag": "罪悪感",
            "life_scene": "家庭",
            "key_concept": "慈悲",
            "benefit_blurb": "距離を置ける",
            "analogy_image": "光の輪",
        },
    }
    response = client.post("/api/planning", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["columns"]["ターゲット層"] == persona_text
    assert data["columns"]["悩みタグ_メイン"] == "人間関係"
    fields = {item["key"]: item["value"] for item in data["planning"]["fields"]}
    assert fields["description_lead"] == "優しさを利用されがちなあなたへ──慈悲と境界線のお話。"
    assert fields["description_takeaways"] == "・慈悲と甘やかしの違い\n・距離を置く言い換え3つ"

    channels_dir: Path = planning_test_env["channels_dir"]  # type: ignore[assignment]
    channel_csv = channels_dir / "CH01.csv"
    assert channel_csv.exists()
    content = channel_csv.read_text(encoding="utf-8")
    assert "優しさを利用されがちなあなたへ" in content

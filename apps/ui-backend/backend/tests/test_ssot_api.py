from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from test_planning_api import planning_test_env


def test_persona_document_roundtrip(planning_test_env):
    client: TestClient = planning_test_env["client"]  # type: ignore[assignment]
    persona_path = planning_test_env["persona_text_path"]  # type: ignore[assignment]

    response = client.get("/api/ssot/persona/CH01")
    assert response.status_code == 200
    data = response.json()
    assert data["channel"] == "CH01"
    normalized_path = str(data["path"]).replace("\\", "/")
    assert normalized_path.endswith(
        "workspaces/planning/personas/CH01_PERSONA.md"
    ) or normalized_path.endswith("progress/personas/CH01_PERSONA.md")
    assert "仏教の整理術" in data["content"]

    new_content = "# Persona\n> 新しいペルソナテキスト。\n"
    response = client.put("/api/ssot/persona/CH01", json={"content": new_content})
    assert response.status_code == 200
    assert "新しいペルソナテキスト" in response.json()["content"]
    assert "新しいペルソナテキスト" in open(persona_path, encoding="utf-8").read()


@pytest.mark.parametrize(
    "headers",
    [
        [
            "チャンネル",
            "No.",
            "悩みタグ_メイン",
            "悩みタグ_サブ",
            "ライフシーン",
            "キーコンセプト",
            "ベネフィット一言",
            "たとえ話イメージ",
            "説明文_リード",
            "説明文_この動画でわかること",
        ],
    ],
)
def test_template_update_requires_columns(planning_test_env, headers):
    client: TestClient = planning_test_env["client"]  # type: ignore[assignment]

    invalid_content = "チャンネル,No.,タイトル\nCH01,191,テスト\n"
    response = client.put("/api/ssot/templates/CH01", json={"content": invalid_content})
    assert response.status_code == 400
    assert "必須列" in response.json()["detail"]

    valid_header = ",".join(headers)
    sample_row = (
        "CH01,191,人間関係,罪悪感,深夜のベッド,ストア派,"
        "頭の中を静かにできる,雑念を箱に入れる,"
        "考えすぎて眠れない夜に、心を整える哲学の視点を届けます。,"
        "・怒りが湧いた瞬間の対処法\\n・感情を言語化する3ステップ"
    )
    valid_content = f"{valid_header}\n{sample_row}\n"
    response = client.put("/api/ssot/templates/CH01", json={"content": valid_content})
    assert response.status_code == 200
    data = response.json()
    assert data["headers"][0] == "チャンネル"
    assert "人間関係" in data["content"]

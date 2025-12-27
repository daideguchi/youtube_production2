from __future__ import annotations

from pathlib import Path

from scripts.ops.planning_lint import lint_planning_csv


def test_planning_lint_handles_multiline_quoted_cells(tmp_path: Path) -> None:
    csv_path = tmp_path / "CH03.csv"
    # Multiline planning fields are common (human-written). The CSV reader must
    # treat embedded newlines inside quoted cells as part of the same row.
    csv_path.write_text(
        (
            "動画番号,タイトル,企画意図,ターゲット層,具体的な内容（話の構成案）,DALL-Eプロンプト（URL・テキスト指示込み）\n"
            "1,【健康】テスト,テスト企画,60代以上,\"導入\n中盤\n終盤\",https://example.invalid\n"
        ),
        encoding="utf-8",
    )

    report = lint_planning_csv(csv_path, "CH03")
    issues = report.get("issues") or []

    assert report["ok"] is True
    assert not any(it.get("code") == "missing_required_columns" for it in issues if isinstance(it, dict))
    assert not any(it.get("code") == "missing_required_field" for it in issues if isinstance(it, dict))


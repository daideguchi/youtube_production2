from __future__ import annotations

import csv
from pathlib import Path

from scripts.ops.planning_lint import lint_planning_csv


def _write_csv(path: Path, *, headers: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def test_planning_lint_tag_mismatch_is_warning_by_default(tmp_path: Path) -> None:
    csv_path = tmp_path / "CH99.csv"
    _write_csv(
        csv_path,
        headers=["動画番号", "タイトル", "内容（企画要約）"],
        rows=[
            {"動画番号": "1", "タイトル": "【東京】テスト", "内容（企画要約）": "【大阪】テスト"},
        ],
    )
    report = lint_planning_csv(csv_path, "CH99", tag_mismatch_is_error=False)
    issues = report.get("issues") or []
    tag_issues = [it for it in issues if isinstance(it, dict) and it.get("code") == "tag_mismatch_title_vs_content_summary"]
    assert tag_issues, "expected tag mismatch issue"
    assert tag_issues[0]["severity"] == "warning"
    assert report["ok"] is True


def test_planning_lint_tag_mismatch_can_be_error(tmp_path: Path) -> None:
    csv_path = tmp_path / "CH99.csv"
    _write_csv(
        csv_path,
        headers=["動画番号", "タイトル", "内容（企画要約）"],
        rows=[
            {"動画番号": "1", "タイトル": "【東京】テスト", "内容（企画要約）": "【大阪】テスト"},
        ],
    )
    report = lint_planning_csv(csv_path, "CH99", tag_mismatch_is_error=True)
    issues = report.get("issues") or []
    tag_issues = [it for it in issues if isinstance(it, dict) and it.get("code") == "tag_mismatch_title_vs_content_summary"]
    assert tag_issues, "expected tag mismatch issue"
    assert tag_issues[0]["severity"] == "error"
    assert report["ok"] is False


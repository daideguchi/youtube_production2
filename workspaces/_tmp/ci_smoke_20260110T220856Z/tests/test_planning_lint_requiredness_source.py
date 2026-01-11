from __future__ import annotations

from pathlib import Path

from scripts.ops.planning_lint import lint_planning_csv


def test_planning_lint_required_columns_follow_planning_requirements(tmp_path: Path) -> None:
    """
    planning_lint must align with UI planning guard:
    required fields come from `planning_requirements`, not `channels.json`.
    """
    csv_path = tmp_path / "CH03.csv"
    csv_path.write_text("動画番号,タイトル\n1,テスト\n", encoding="utf-8")

    report = lint_planning_csv(csv_path, "CH03")
    assert report["ok"] is True


def test_planning_lint_enforces_planning_requirements_after_min_no(tmp_path: Path) -> None:
    csv_path = tmp_path / "CH03.csv"
    # CH03 requires TAG_REQUIREMENT_KEYS starting from No.101 (planning_requirements).
    csv_path.write_text("動画番号,タイトル\n101,テスト\n", encoding="utf-8")

    report = lint_planning_csv(csv_path, "CH03")
    issues = report.get("issues") or []
    codes = [it.get("code") for it in issues if isinstance(it, dict)]

    assert "missing_required_columns_by_policy" in codes
    assert report["ok"] is False


from __future__ import annotations

from factory_common.fact_check import (
    FACT_CHECK_LOGIC_VERSION,
    FACT_CHECK_REPORT_SCHEMA,
    extract_candidate_claims,
    run_fact_check_with_codex,
)


def test_extract_candidate_claims_splits_japanese_without_spaces() -> None:
    text = "統計によると2020年のデータは10%です。2021年は12%です。"
    claims = extract_candidate_claims(text, max_claims=12, min_score=4)
    assert len(claims) >= 2
    assert all("\n" not in c.get("claim", "") for c in claims)


def test_fact_check_passes_when_no_checkable_claims(tmp_path) -> None:
    search_results_path = tmp_path / "search_results.json"
    wikipedia_summary_path = tmp_path / "wikipedia_summary.json"
    references_path = tmp_path / "references.json"
    output_path = tmp_path / "fact_check_report.json"

    search_results_path.write_text("{}\n", encoding="utf-8")
    wikipedia_summary_path.write_text("{}\n", encoding="utf-8")
    references_path.write_text("[]\n", encoding="utf-8")

    report = run_fact_check_with_codex(
        channel="CH13",
        video="001",
        a_text="今日は少し呼吸を整えてみましょう。",
        policy="required",
        search_results_path=search_results_path,
        wikipedia_summary_path=wikipedia_summary_path,
        references_path=references_path,
        output_path=output_path,
    )

    assert report["schema"] == FACT_CHECK_REPORT_SCHEMA
    assert report["logic_version"] == FACT_CHECK_LOGIC_VERSION
    assert report["verdict"] == "pass"
    assert report.get("note") == "no_checkable_claims"
    assert report.get("claims") == []
    assert output_path.exists()


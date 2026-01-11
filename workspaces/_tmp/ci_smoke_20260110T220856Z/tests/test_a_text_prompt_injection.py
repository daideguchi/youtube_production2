from __future__ import annotations

from pathlib import Path

from script_pipeline import runner as sp_runner


def test_extract_a_text_channel_prompt_for_llm_keeps_block_bullets_and_drops_formatting_rules() -> None:
    raw = """
# Channel Prompt

役割: テスト用。

禁止:
- 固有名詞は出さない
- 誹謗中傷は禁止
- 断定的な医療助言は禁止
- 暴力/復讐の推奨は禁止
- 視聴者を煽る表現は禁止
- 数字や統計の断定は禁止
- 相手を裁く結論は禁止
- スカッと勝利の結末は禁止

構造:
1) 導入
2) 背景

出力仕様:
- 見出しは禁止
- ポーズ記号は `---` のみ
""".strip()

    out = sp_runner._extract_a_text_channel_prompt_for_llm(raw)
    assert "禁止" in out
    assert "固有名詞は出さない" in out
    assert "誹謗中傷は禁止" in out
    # Drop step/structure details and formatting directives.
    assert "導入" not in out
    assert "背景" not in out
    assert "ポーズ記号" not in out
    assert "見出しは禁止" not in out


def test_extract_a_text_channel_prompt_for_llm_integration_ch23_contains_constraints() -> None:
    prompt_path = Path("packages/script_pipeline/channels/CH23-熟年夫婦の現実ノート/script_prompt.txt")
    raw = prompt_path.read_text(encoding="utf-8")

    out = sp_runner._extract_a_text_channel_prompt_for_llm(raw)
    # Must keep key constraint blocks (these are frequently lost when heuristics over-prune).
    assert "題材の境界" in out
    assert "友人コミュニティ" in out
    assert "禁止" in out
    assert "暴力/復讐" in out
    # Must drop hard format/structure directives to avoid SSOT collisions.
    assert "出力仕様" not in out
    assert "ポーズ記号" not in out
    assert "構造" not in out


def test_extract_a_text_benchmark_excerpts_for_llm_ch23_includes_handle_and_drops_ui_meta() -> None:
    prompt_path = Path("packages/script_pipeline/channels/CH23-熟年夫婦の現実ノート/script_prompt.txt")
    out = sp_runner._extract_a_text_benchmark_excerpts_for_llm(prompt_path)

    assert "@kokoroshiawase" in out
    assert "CH23:" in out
    assert "UIで更新" not in out


def test_a_text_prompt_templates_include_benchmark_excerpts_placeholder() -> None:
    seed_tpl = Path("packages/script_pipeline/prompts/a_text_seed_prompt.txt").read_text(encoding="utf-8")
    rebuild_tpl = Path("packages/script_pipeline/prompts/a_text_rebuild_draft_prompt.txt").read_text(encoding="utf-8")

    assert "<<BENCHMARK_EXCERPTS>>" in seed_tpl
    assert "<<BENCHMARK_EXCERPTS>>" in rebuild_tpl

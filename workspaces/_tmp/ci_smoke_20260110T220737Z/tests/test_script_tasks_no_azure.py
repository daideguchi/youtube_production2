"""
Guardrail: A-text writing tasks must never route to Azure GPT models.

Rationale:
  - This repo treats Azure GPT (e.g. gpt-5-mini) as forbidden for script A-text writing.
  - Prevents accidental reintroduction via task overrides or tier candidate drift.
"""

from factory_common.llm_config import load_llm_config, resolve_task


def test_a_text_tasks_do_not_route_to_azure_gpt5_mini():
    cfg = load_llm_config()

    forbidden_models = {"azure_gpt5_mini"}
    tasks = [
        "script_chapter_review",
        "script_a_text_seed",
        "script_a_text_quality_fix",
        "script_a_text_quality_extend",
        "script_a_text_quality_expand",
        "script_a_text_quality_shrink",
        "script_a_text_final_polish",
        "script_a_text_rebuild_plan",
        "script_a_text_rebuild_draft",
        "script_semantic_alignment_fix",
    ]

    for t in tasks:
        info = resolve_task(cfg, t)
        models = info.get("models", [])
        assert isinstance(models, list), f"Task {t} should resolve to models list"
        assert not (forbidden_models & set(models)), f"Task {t} routes to forbidden models: {models}"

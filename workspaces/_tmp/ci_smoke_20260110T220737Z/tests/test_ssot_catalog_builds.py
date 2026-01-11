from factory_common.ssot_catalog import CATALOG_SCHEMA_V1, build_ssot_catalog


def test_ssot_catalog_builds_and_has_no_missing_task_defs():
    cat = build_ssot_catalog()
    assert cat.get("schema") == CATALOG_SCHEMA_V1
    assert (cat.get("llm") or {}).get("missing_task_defs") == []


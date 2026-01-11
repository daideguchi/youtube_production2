from __future__ import annotations

import re

from factory_common import paths


def _extract_issue_codes(relative_path: str) -> set[str]:
    text = (paths.repo_root() / relative_path).read_text(encoding="utf-8")
    return set(re.findall(r'code="([^"]+)"', text))


def _extract_hint_keys() -> set[str]:
    text = (paths.repo_root() / "scripts/ops/preproduction_issue_catalog.py").read_text(encoding="utf-8")
    return set(re.findall(r'^\s*"([^"]+)"\s*:\s*\[', text, flags=re.M))


def test_all_preproduction_issue_codes_have_fix_hints():
    codes = _extract_issue_codes("scripts/ops/production_pack.py") | _extract_issue_codes("scripts/ops/preproduction_audit.py")
    hint_keys = _extract_hint_keys()
    missing = sorted(codes - hint_keys)
    assert missing == [], f"missing fix_hints for issue codes: {missing}"


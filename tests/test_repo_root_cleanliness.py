from __future__ import annotations

from factory_common.paths import repo_root
from factory_common.repo_layout import unexpected_repo_root_files


def test_repo_root_has_no_unexpected_files() -> None:
    root = repo_root()
    unexpected = unexpected_repo_root_files(root)
    assert not unexpected, f"Unexpected repo-root files: {[p.name for p in unexpected]}"


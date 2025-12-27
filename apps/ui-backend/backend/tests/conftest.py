from __future__ import annotations

from _bootstrap import bootstrap


def pytest_configure() -> None:
    """
    Ensure monorepo imports work without root-level alias symlinks.

    - Adds repo_root/ and repo_root/packages/ via `_bootstrap.bootstrap()`
    - `backend` imports are handled by pytest `pythonpath` (see pyproject.toml)
    """
    bootstrap(load_env=False)

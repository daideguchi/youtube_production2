from __future__ import annotations

import sys

from _bootstrap import bootstrap


def pytest_configure() -> None:
    """
    Ensure monorepo imports work without root-level alias symlinks.

    - Adds repo_root/ and repo_root/packages/ via `_bootstrap.bootstrap()`
    - Adds repo_root/apps/ui-backend so `import backend` works in tests
    """
    repo_root = bootstrap(load_env=False)
    backend_parent = repo_root / "apps" / "ui-backend"
    if str(backend_parent) not in sys.path:
        sys.path.insert(0, str(backend_parent))


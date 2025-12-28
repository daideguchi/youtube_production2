"""
packages/ namespace compatibility shim.

Some ad-hoc tools/snippets import modules as `packages.<pkg>...` from the repo root.
In that execution mode, `packages/` itself is importable (repo root is on sys.path),
but the *contents* of `packages/` are not, so `import factory_common` can fail.

To reduce friction for operators/agents, ensure `packages/` is on sys.path when this
namespace is imported.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _ensure_packages_on_syspath() -> None:
    packages_dir = Path(__file__).resolve().parent
    packages_dir_str = str(packages_dir)
    if packages_dir_str not in sys.path:
        sys.path.insert(0, packages_dir_str)


_ensure_packages_on_syspath()


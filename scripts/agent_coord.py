#!/usr/bin/env python3
"""
DEPRECATED (compat wrapper)
==========================
`scripts/agent_coord.py` is deprecated. Use `scripts/agent_org.py` instead.

This file is kept as a thin compatibility wrapper so old commands keep working:
  python3 scripts/agent_coord.py locks
  python3 scripts/agent_coord.py memo --to "*" --subject "..." --body "..."
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _find_repo_root(start: Path) -> Path:
    cur = start if start.is_dir() else start.parent
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate.resolve()
    return cur.resolve()


def main() -> int:
    root = _find_repo_root(Path(__file__).resolve())
    target = root / "scripts" / "agent_org.py"
    cmd = [sys.executable, str(target), *sys.argv[1:]]
    return subprocess.call(cmd, cwd=root)


if __name__ == "__main__":
    raise SystemExit(main())

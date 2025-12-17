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

from factory_common.paths import repo_root


def main() -> int:
    root = repo_root()
    target = root / "scripts" / "agent_org.py"
    cmd = [sys.executable, str(target), *sys.argv[1:]]
    return subprocess.call(cmd, cwd=root)


if __name__ == "__main__":
    raise SystemExit(main())


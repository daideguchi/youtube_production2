#!/usr/bin/env python3
"""
lint_llm_config — compatibility shim (DO NOT USE FOR NEW WORK)

History:
  - The repo previously used `scripts/ops/lint_llm_config.py` for legacy `configs/llm.yml` linting.
  - The current SSOT is slot-based routing: `configs/llm_router.yaml` (+ codes/slots/overrides).

This file is kept only to avoid CI / docs drift breaking older references.
It forwards to: `scripts/ops/lint_llm_router_config.py`.
"""

from __future__ import annotations

import subprocess
import sys

from _bootstrap import bootstrap

bootstrap(load_env=False)

from factory_common.paths import repo_root  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    target = repo_root() / "scripts" / "ops" / "lint_llm_router_config.py"
    print(
        "[lint_llm_config] deprecated/compat shim: forwarding to scripts/ops/lint_llm_router_config.py",
        file=sys.stderr,
    )
    p = subprocess.run([sys.executable, str(target), *args], check=False)
    return int(p.returncode)


if __name__ == "__main__":
    raise SystemExit(main())


"""
UI backend package bootstrap.

This repo is a monorepo with shared packages under `<repo>/packages/`.
When running UI backend commands from `apps/ui-backend/` (e.g. `uvicorn backend.main:app`),
Python may not include the repo root / packages on `sys.path`, which breaks imports like:
  - `import audio_tts_v2`
  - `import factory_common`

We bootstrap `sys.path` and fail-soft load `<repo>/.env` to match repo-root behavior.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _load_env_files(paths: list[Path]) -> None:
    for env_path in paths:
        if not env_path.exists():
            continue
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, value = stripped.split("=", 1)
                key = key.strip()
                value = value.strip().strip("\"'")
                os.environ.setdefault(key, value)
        except Exception:
            continue


PROJECT_ROOT = Path(__file__).resolve().parents[3]
PACKAGES_ROOT = PROJECT_ROOT / "packages"

for candidate in (PROJECT_ROOT, PACKAGES_ROOT):
    if candidate.exists():
        candidate_str = str(candidate)
        if candidate_str not in sys.path:
            sys.path.insert(0, candidate_str)

_load_env_files([PROJECT_ROOT / ".env", Path.home() / ".env"])

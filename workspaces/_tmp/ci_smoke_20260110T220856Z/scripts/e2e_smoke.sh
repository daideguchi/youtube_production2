#!/usr/bin/env bash
set -euo pipefail

# Lightweight E2E smoke entrypoint (opt-in via RUN_E2E_SMOKE=1).
# This does NOT run heavy pipelines; it only triggers the gated smoke tests.

if [ "${RUN_E2E_SMOKE:-0}" != "1" ]; then
  echo "RUN_E2E_SMOKE is not set to 1; skipping E2E smoke."
  exit 0
fi

echo "[INFO] RUN_E2E_SMOKE=1 → running lightweight config tests only (no heavy pipelines)."

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PYTHONPATH=. pytest \
  tests/test_e2e_env_gate.py \
  tests/test_e2e_smoke_placeholder.py \
  tests/test_image_models_config.py \
  tests/test_visual_tasks_routing.py \
  tests/test_nanobanana_mode_clamp.py \
  "$@"

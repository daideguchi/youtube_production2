#!/usr/bin/env bash
set -euo pipefail

# Cleanup local caches that only add noise.
# Safe: removes only re-generatable caches (no SoT).

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

EXCLUDES=(
  "./.venv"
  "./apps/ui-frontend/node_modules"
  "./apps/remotion/node_modules"
)

_find_prune_expr=()
for p in "${EXCLUDES[@]}"; do
  _find_prune_expr+=( -path "$p" -o )
done
unset '_find_prune_expr[${#_find_prune_expr[@]}-1]' 2>/dev/null || true

# __pycache__
if [ "${#_find_prune_expr[@]}" -gt 0 ]; then
  find . \( "${_find_prune_expr[@]}" \) -prune -o -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
else
  find . -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
fi

# .pytest_cache
if [ "${#_find_prune_expr[@]}" -gt 0 ]; then
  find . \( "${_find_prune_expr[@]}" \) -prune -o -type d -name '.pytest_cache' -prune -exec rm -rf {} + 2>/dev/null || true
else
  find . -type d -name '.pytest_cache' -prune -exec rm -rf {} + 2>/dev/null || true
fi

# .DS_Store
if [ "${#_find_prune_expr[@]}" -gt 0 ]; then
  find . \( "${_find_prune_expr[@]}" \) -prune -o -type f -name '.DS_Store' -delete 2>/dev/null || true
else
  find . -type f -name '.DS_Store' -delete 2>/dev/null || true
fi

echo "[ok] cleaned __pycache__/.pytest_cache/.DS_Store"


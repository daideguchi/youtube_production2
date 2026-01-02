#!/usr/bin/env bash
# Ensures all factory_commentary automation commands inherit the shared .env
# Usage: ./scripts/with_ytm_env.sh <command> [args...]
# Example: ./scripts/with_ytm_env.sh python3 scripts/check_env.py
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/.env"

# Prefer repo-local virtualenv when present (Homebrew Python is PEP668 externally-managed).
VENV_DIR="$ROOT_DIR/.venv"
if [[ -x "$VENV_DIR/bin/python3" || -x "$VENV_DIR/bin/python" ]]; then
  export PATH="$VENV_DIR/bin:$PATH"
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "❌ .env not found at $ENV_FILE" >&2
  exit 1
fi
# Ensure repo imports work regardless of caller CWD/global PYTHONPATH.
export PYTHONPATH="$ROOT_DIR:$ROOT_DIR/packages"
# shellcheck disable=SC1090
set -a
source "$ENV_FILE"
set +a

# Default: disable LLM-backed web search (cost control). Override per-run if needed:
#   YTM_WEB_SEARCH_PROVIDER=brave ./scripts/with_ytm_env.sh ...
: "${YTM_WEB_SEARCH_PROVIDER:=disabled}"
export YTM_WEB_SEARCH_PROVIDER
if [[ $# -eq 0 ]]; then
  echo "✅ Environment loaded. Run commands like: ./scripts/with_ytm_env.sh python3 ..." >&2
  exit 0
fi
exec "$@"

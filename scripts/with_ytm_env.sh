#!/usr/bin/env bash
# Ensures all factory_commentary automation commands inherit the shared .env
# Usage: ./scripts/with_ytm_env.sh <command> [args...]
# Example: ./scripts/with_ytm_env.sh python3 scripts/check_env.py
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/.env"
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
if [[ $# -eq 0 ]]; then
  echo "✅ Environment loaded. Run commands like: ./scripts/with_ytm_env.sh python3 ..." >&2
  exit 0
fi
exec "$@"

#!/usr/bin/env bash
# Ensures all factory_commentary automation commands inherit the shared .env
# Usage: ./scripts/with_ytm_env.sh <command> [args...]
# Example: ./scripts/with_ytm_env.sh python3 scripts/check_env.py
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/.env"
PROJECT_ENV_DIR="$ROOT_DIR/packages/commentary_02_srt2images_timeline/env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "❌ .env not found at $ENV_FILE" >&2
  exit 1
fi
# Ensure repo imports work regardless of caller CWD/global PYTHONPATH.
export PYTHONPATH="$ROOT_DIR:$ROOT_DIR/packages${PYTHONPATH:+:$PYTHONPATH}"
# shellcheck disable=SC1090
set -a
source "$ENV_FILE"
# Load optional per-project env overrides (e.g. commentary_02_srt2images_timeline/env/*.env)
if [[ -d "$PROJECT_ENV_DIR" ]]; then
  while IFS= read -r -d '' env_path; do
    [[ -f "$env_path" ]] || continue
    # shellcheck disable=SC1090
    source "$env_path"
  done < <(find "$PROJECT_ENV_DIR" -maxdepth 1 -type f -name '*.env' -print0 | sort -z)
fi
set +a
if [[ $# -eq 0 ]]; then
  echo "✅ Environment loaded. Run commands like: ./scripts/with_ytm_env.sh python3 ..." >&2
  exit 0
fi
exec "$@"

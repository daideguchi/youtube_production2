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

# Optional: numeric slots (operator-friendly; avoids editing configs).
#
# - LLM model slot (what model code each tier uses):
#     LLM_MODEL_SLOT   (configs/llm_model_slots.yaml)
# - LLM exec slot (where/how it runs: api/codex exec/think/agent/failover):
#     LLM_EXEC_SLOT    (configs/llm_exec_slots.yaml)
#
# Examples:
#   ./scripts/with_ytm_env.sh --llm-slot 2 python3 ...
#   ./scripts/with_ytm_env.sh --exec-slot 3 python3 ...
#   ./scripts/with_ytm_env.sh x3 2 python3 ...   # shorthand: xN=exec slot, N=model slot
while [[ $# -ge 1 ]]; do
  case "$1" in
    --llm-slot|--model-slot)
      if [[ $# -lt 2 ]]; then
        echo "❌ Missing value for $1 (expected an integer slot id)" >&2
        exit 2
      fi
      if [[ ! "$2" =~ ^[0-9]+$ ]]; then
        echo "❌ Invalid LLM slot: $2 (expected an integer)" >&2
        exit 2
      fi
      export LLM_MODEL_SLOT="$2"
      shift 2
      ;;
    --llm-slot=*|--model-slot=*)
      SLOT_VAL="${1#*=}"
      if [[ ! "$SLOT_VAL" =~ ^[0-9]+$ ]]; then
        echo "❌ Invalid LLM slot: $SLOT_VAL (expected an integer)" >&2
        exit 2
      fi
      export LLM_MODEL_SLOT="$SLOT_VAL"
      shift 1
      ;;
    --exec-slot|--llm-exec-slot)
      if [[ $# -lt 2 ]]; then
        echo "❌ Missing value for $1 (expected an integer slot id)" >&2
        exit 2
      fi
      if [[ ! "$2" =~ ^[0-9]+$ ]]; then
        echo "❌ Invalid exec slot: $2 (expected an integer)" >&2
        exit 2
      fi
      export LLM_EXEC_SLOT="$2"
      shift 2
      ;;
    --exec-slot=*|--llm-exec-slot=*)
      EXEC_VAL="${1#*=}"
      if [[ ! "$EXEC_VAL" =~ ^[0-9]+$ ]]; then
        echo "❌ Invalid exec slot: $EXEC_VAL (expected an integer)" >&2
        exit 2
      fi
      export LLM_EXEC_SLOT="$EXEC_VAL"
      shift 1
      ;;
    x*)
      EXEC_VAL="${1#x}"
      if [[ "$EXEC_VAL" =~ ^[0-9]+$ ]]; then
        export LLM_EXEC_SLOT="$EXEC_VAL"
        shift 1
        continue
      fi
      break
      ;;
    *)
      if [[ "$1" =~ ^[0-9]+$ ]]; then
        export LLM_MODEL_SLOT="$1"
        shift 1
        continue
      fi
      break
      ;;
  esac
done
if [[ $# -eq 0 ]]; then
  echo "✅ Environment loaded. Run commands like: ./scripts/with_ytm_env.sh python3 ..." >&2
  exit 0
fi
exec "$@"

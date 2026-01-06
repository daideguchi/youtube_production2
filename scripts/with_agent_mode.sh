#!/usr/bin/env bash
# Usage:
#   ./scripts/with_agent_mode.sh [--tasks a,b] [--prefixes p1,p2] [--exclude-tasks a,b] [--exclude-prefixes p1,p2] [--queue-dir path] -- <command> [args...]
set -euo pipefail

usage() {
  cat <<'USAGE' >&2
with_agent_mode.sh — run a command in AGENT MODE (via LLM_EXEC_SLOT)

Usage:
  ./scripts/with_agent_mode.sh [options] -- <command> [args...]

Options:
  --tasks <csv>             Set LLM_AGENT_TASKS (exact allowlist)
  --prefixes <csv>          Set LLM_AGENT_TASK_PREFIXES (prefix allowlist)
  --exclude-tasks <csv>     Set LLM_AGENT_EXCLUDE_TASKS (exact blocklist)
  --exclude-prefixes <csv>  Set LLM_AGENT_EXCLUDE_PREFIXES (prefix blocklist)
  --queue-dir <path>        Set LLM_AGENT_QUEUE_DIR
  --runbooks <path>         Set LLM_AGENT_RUNBOOKS_CONFIG

Example (script tasks only):
  ./scripts/with_ytm_env.sh bash scripts/with_agent_mode.sh --prefixes script_ -- \
    python -m script_pipeline.cli run-all --channel CH06 --video 033
USAGE
}

# Compatibility: callers may still export LLM_MODE=think/agent.
# In normal ops we avoid LLM_MODE and use exec-slot to prevent drift across agents.
MODE_RAW="${LLM_MODE:-agent}"
unset LLM_MODE || true
case "$(echo "$MODE_RAW" | tr '[:upper:]' '[:lower:]')" in
  think)
    export LLM_EXEC_SLOT="3"
    ;;
  agent|"")
    export LLM_EXEC_SLOT="4"
    ;;
  *)
    echo "Invalid LLM_MODE for with_agent_mode.sh: $MODE_RAW (expected: agent|think)" >&2
    exit 2
    ;;
esac

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tasks)
      export LLM_AGENT_TASKS="${2:-}"
      shift 2
      ;;
    --prefixes)
      export LLM_AGENT_TASK_PREFIXES="${2:-}"
      shift 2
      ;;
    --exclude-tasks)
      export LLM_AGENT_EXCLUDE_TASKS="${2:-}"
      shift 2
      ;;
    --exclude-prefixes)
      export LLM_AGENT_EXCLUDE_PREFIXES="${2:-}"
      shift 2
      ;;
    --queue-dir)
      export LLM_AGENT_QUEUE_DIR="${2:-}"
      shift 2
      ;;
    --runbooks)
      export LLM_AGENT_RUNBOOKS_CONFIG="${2:-}"
      shift 2
      ;;
    --)
      shift
      break
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ $# -eq 0 ]]; then
  usage
  exit 2
fi

exec "$@"

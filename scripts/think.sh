#!/usr/bin/env bash
# THINK MODE — "エージェントが思考するモード"
# - One-shot wrapper: loads .env, enables agent queue, runs your command.
# - When the command stops due to pending tasks, it prints the queue and (optionally) writes bundle files.
#
# Usage:
#   ./scripts/think.sh [--script|--tts|--visual|--all-text] [--agent-name <name>] [--no-bundle] [--loop] [--] <command> [args...]
#
# Examples:
#   ./scripts/think.sh --script -- python -m script_pipeline.cli run-all --channel CH06 --video 033
#   ./scripts/think.sh --tts -- python -m script_pipeline.cli audio --channel CH06 --video 033
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Defaults: text tasks only (avoid image generation tasks)
PREFIXES_DEFAULT="script_,tts_,visual_,title_,belt_"
EXCLUDE_TASKS_DEFAULT="visual_image_gen,image_generation"
AUTO_BUNDLE=1
LOOP=0
SLEEP_SEC=2
MAX_ITER=200

PREFIXES="$PREFIXES_DEFAULT"
EXCLUDE_TASKS="$EXCLUDE_TASKS_DEFAULT"
AGENT_NAME_OVERRIDE=""

usage() {
  cat <<'USAGE' >&2
THINK MODE — "エージェントが思考するモード"

Usage:
  ./scripts/think.sh [--script|--tts|--visual|--all-text] [--agent-name <name>] [--no-bundle] [--] <command> [args...]

Options:
  --script        Only intercept script_* tasks
  --tts           Only intercept tts_* tasks
  --visual        Only intercept visual_* tasks (text only; image_generation is excluded)
  --all-text      Intercept script_/tts_/visual_/title_/belt_ (default)
  --agent-name <name>
                 Set LLM_AGENT_NAME for claim/completed_by metadata
  --no-bundle     Do not generate bundle markdown files for pending tasks
  --loop          Keep rerunning until pending clears and command succeeds
  --sleep <sec>   Poll interval for --loop (default: 2)
  --max-iter <n>  Max reruns for --loop (default: 200)
  -h|--help       Show help

Notes:
  - Results are written under workspaces/logs/agent_tasks/ (or LLM_AGENT_QUEUE_DIR)
  - Use python scripts/agent_runner.py list/show/prompt/chat/bundle/complete to manage tasks.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --script)
      PREFIXES="script_"
      shift
      ;;
    --tts)
      PREFIXES="tts_"
      shift
      ;;
    --visual)
      PREFIXES="visual_"
      shift
      ;;
    --all-text)
      PREFIXES="$PREFIXES_DEFAULT"
      shift
      ;;
    --agent-name)
      if [[ -z "${2:-}" ]]; then
        echo "missing value for --agent-name" >&2
        usage
        exit 2
      fi
      AGENT_NAME_OVERRIDE="$2"
      shift 2
      ;;
    --no-bundle)
      AUTO_BUNDLE=0
      shift
      ;;
    --loop)
      LOOP=1
      shift
      ;;
    --sleep)
      SLEEP_SEC="${2:-2}"
      shift 2
      ;;
    --max-iter)
      MAX_ITER="${2:-200}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    # First non-option = command
    *)
      break
      ;;
  esac
done

if [[ $# -eq 0 ]]; then
  usage
  exit 2
fi

CMD=("$@")

DEFAULT_LOGS_DIR="$ROOT_DIR/workspaces/logs"
if [[ ! -d "$DEFAULT_LOGS_DIR" ]]; then
  DEFAULT_LOGS_DIR="$ROOT_DIR/logs"
fi

QUEUE_DIR="${LLM_AGENT_QUEUE_DIR:-$DEFAULT_LOGS_DIR/agent_tasks}"
PENDING_DIR="$QUEUE_DIR/pending"

iter=0
while :; do
  iter=$((iter + 1))
  if [[ "$LOOP" == "1" && "$iter" -gt "$MAX_ITER" ]]; then
    echo "[THINK MODE] max iterations reached (${MAX_ITER}); stopping." >&2
    exit 3
  fi

  ENV_VARS=(
    LLM_MODE=think
    LLM_AGENT_QUEUE_DIR="$QUEUE_DIR"
    LLM_AGENT_TASK_PREFIXES="$PREFIXES"
    LLM_AGENT_EXCLUDE_TASKS="$EXCLUDE_TASKS"
  )
  if [[ -n "$AGENT_NAME_OVERRIDE" ]]; then
    ENV_VARS+=(LLM_AGENT_NAME="$AGENT_NAME_OVERRIDE")
  fi

  set +e
  "$ROOT_DIR/scripts/with_ytm_env.sh" \
    env \
      "${ENV_VARS[@]}" \
      "${CMD[@]}"
  EXIT_CODE=$?
  set -e

  # Command succeeded → done
  if [[ "$EXIT_CODE" -eq 0 ]]; then
    exit 0
  fi

  # If pending exists, show it (and optionally wait in loop mode)
  if [[ -d "$PENDING_DIR" ]]; then
    shopt -s nullglob
    pending_files=("$PENDING_DIR"/*.json)
    shopt -u nullglob
  else
    pending_files=()
  fi

  if [[ ${#pending_files[@]} -eq 0 ]]; then
    # Non-agent failure → propagate
    exit "$EXIT_CODE"
  fi

  echo "" >&2
  echo "==============================" >&2
  echo "THINK MODE: pending tasks found" >&2
  echo "==============================" >&2
  python "$ROOT_DIR/scripts/agent_runner.py" --queue-dir "$QUEUE_DIR" list >&2 || true

  if [[ "$AUTO_BUNDLE" == "1" ]]; then
    for f in "${pending_files[@]}"; do
      id="$(basename "$f" .json)"
      python "$ROOT_DIR/scripts/agent_runner.py" --queue-dir "$QUEUE_DIR" bundle "$id" --include-runbook >/dev/null 2>&1 || true
    done
    echo "" >&2
    echo "bundle written: $QUEUE_DIR/bundles/*.md" >&2
  fi

  echo "" >&2
  echo "next:" >&2
  echo "  - python scripts/agent_runner.py prompt <TASK_ID>" >&2
  echo "  - python scripts/agent_runner.py complete <TASK_ID> --content-file /path/to/content.txt" >&2
  if [[ "$LOOP" == "1" ]]; then
    echo "" >&2
    echo "[THINK MODE] waiting for pending to clear..." >&2
    while :; do
      if [[ ! -d "$PENDING_DIR" ]]; then
        break
      fi
      shopt -s nullglob
      still=("$PENDING_DIR"/*.json)
      shopt -u nullglob
      if [[ ${#still[@]} -eq 0 ]]; then
        break
      fi
      sleep "$SLEEP_SEC"
    done
    echo "[THINK MODE] pending cleared; rerunning..." >&2
    continue
  fi

  echo "  - rerun the same command to continue" >&2
  exit "$EXIT_CODE"
done

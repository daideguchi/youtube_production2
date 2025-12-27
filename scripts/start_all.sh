#!/usr/bin/env bash
set -euo pipefail

YTM_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}" )/.." && pwd)"
ENV_FILE="${ENV_FILE:-$YTM_ROOT/.env}"
LOG_ROOT="$YTM_ROOT/workspaces/logs/ui_hub"
BACKEND_REQUIREMENTS="$YTM_ROOT/apps/ui-backend/backend/requirements.txt"
PYTHON_BIN="$YTM_ROOT/.venv/bin/python3"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="python3"
fi
PIP_OPTS="--quiet"
if "$PYTHON_BIN" -m pip --version 2>/dev/null | grep -q "externally managed"; then
  PIP_OPTS="--user --quiet"
fi
mkdir -p "$LOG_ROOT"

info() { printf '\e[32m[INFO]\e[0m %s\n' "$*"; }
warn() { printf '\e[33m[WARN]\e[0m %s\n' "$*"; }
err()  { printf '\e[31m[ERR ]\e[0m %s\n' "$*"; }

# Load a dotenv-style env file (KEY=VALUE) without executing it as shell code.
# This prevents syntax errors when values contain characters like ';'.
load_env_file() {
  local env_path="$1"
  local line key value first_char last_char

  while IFS= read -r line || [ -n "$line" ]; do
    # Trim CRLF and surrounding whitespace
    line="${line%$'\r'}"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"

    [ -z "$line" ] && continue
    case "$line" in
      \#*) continue ;;
    esac

    # Support optional leading "export "
    if [[ "$line" == export[[:space:]]* ]]; then
      line="${line#export }"
      line="${line#"${line%%[![:space:]]*}"}"
    fi

    [[ "$line" == *"="* ]] || continue
    key="${line%%=*}"
    value="${line#*=}"

    # Trim spaces around key/value (dotenv semantics)
    key="${key#"${key%%[![:space:]]*}"}"
    key="${key%"${key##*[![:space:]]}"}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"

    # Skip invalid keys
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue

    # Strip a single pair of surrounding quotes if present.
    if [ "${#value}" -ge 2 ]; then
      first_char="${value:0:1}"
      last_char="${value: -1}"
      if { [ "$first_char" = "\"" ] && [ "$last_char" = "\"" ]; } || { [ "$first_char" = "'" ] && [ "$last_char" = "'" ]; }; then
        value="${value:1:${#value}-2}"
      fi
    fi

    export "$key=$value"
  done < "$env_path"
}

# optional: start remotion preview server (port 3100) for Studio iframe
start_remotion_studio() {
  local remotion_dir="$YTM_ROOT/apps/remotion"
  local log_file="$LOG_ROOT/remotion_studio.log"
  local port="${REMOTION_STUDIO_PORT:-3100}"
  if command -v lsof >/dev/null 2>&1; then
    if lsof -i :"$port" >/dev/null 2>&1; then
      warn "Port $port is in use. Forcing existing process to exit."
      # kill only processes listening on the port
      pids=$(lsof -ti tcp:"$port" || true)
      if [ -n "$pids" ]; then
        kill -9 $pids 2>/dev/null || true
        sleep 0.5
      fi
    fi
  else
    warn "lsof not found; skipping port force-kill for Remotion Studio."
  fi
  if [ ! -d "$remotion_dir" ]; then
    warn "remotion dir not found: $remotion_dir"
    return
  fi
  if [ ! -d "$remotion_dir/node_modules" ]; then
    info "Skipping Remotion Studio (deps not installed). To enable: (cd apps/remotion && npm ci)"
    return
  fi
  # Ensure workspace video input exists (preview reads runs from here).
  mkdir -p "$YTM_ROOT/workspaces/video/input" 2>/dev/null || true
  info "Starting Remotion Studio preview on port $port"
  (
    cd "$remotion_dir"
    BROWSER=none npx remotion preview --entry src/index.ts --root . --public-dir public --port "$port" >>"$log_file" 2>&1 &
    echo $! > "$LOG_ROOT/remotion_studio.pid"
  )
  # wait for server to come up (max ~10s)
  for _ in $(seq 1 20); do
    if curl -sf "http://localhost:${port}/" >/dev/null 2>&1; then
      info "Remotion Studio is up on http://localhost:${port}"
      return
    fi
    sleep 0.5
  done
  info "Remotion Studio not ready yet on port ${port} (optional). Check ${log_file} if you need Studio."
}

main() {
  if [ "$#" -eq 0 ]; then
    set -- start
  fi

  command="$1"
  shift || true

  # Ensure monorepo imports work without root-level symlinks.
  # Keep deterministic; do NOT inherit global PYTHONPATH to avoid legacy import shadowing.
  export PYTHONPATH="$YTM_ROOT:$YTM_ROOT/packages"

  if [[ "$command" == "start" || "$command" == "restart" ]]; then
    # NOTE: env validation is handled by apps/ui-backend/tools/start_manager.py

    # Ensure backend Python deps are present (idempotent)
    if [ -f "$BACKEND_REQUIREMENTS" ]; then
      if "$PYTHON_BIN" -c "import sys; print(int(sys.prefix != getattr(sys, 'base_prefix', sys.prefix)))" 2>/dev/null | grep -q "^1$"; then
        info "Installing backend deps from apps/ui-backend/backend/requirements.txt (idempotent)"
        export PIP_DISABLE_PIP_VERSION_CHECK=1
        "$PYTHON_BIN" -m pip install $PIP_OPTS -r "$BACKEND_REQUIREMENTS" || warn "pip install failed; backend may not start"
      else
        info "Skipping backend deps install (not running in a venv). To enable: python3 -m venv .venv && .venv/bin/pip install -r apps/ui-backend/backend/requirements.txt"
      fi
    fi

    # Keep planning CSV/status in sync for all channels before起動
    if [ -f "$YTM_ROOT/scripts/sync_all_scripts.py" ]; then
      info "Syncing planning/status via sync_all_scripts.py"
      "$PYTHON_BIN" "$YTM_ROOT/scripts/sync_all_scripts.py" \
        > >(grep -v "non-numeric 'No.' rows" || true) \
        2> >(grep -v "non-numeric 'No.' rows" >&2 || true) \
        || warn "sync_all_scripts failed"
    fi

    # Sync audio artifacts into video input (safe: no overwrite)
    if [ -f "$YTM_ROOT/packages/video_pipeline/tools/sync_audio_inputs.py" ]; then
      "$PYTHON_BIN" -m video_pipeline.tools.sync_audio_inputs || warn "sync_audio_inputs failed"
    fi

    if [ -f "$ENV_FILE" ]; then
      info "Loading environment from $ENV_FILE"
      load_env_file "$ENV_FILE" || warn "Failed to parse $ENV_FILE; continuing without sourcing"
    else
      warn "ENV file $ENV_FILE not found; continuing without sourcing"
    fi
  fi

  info "Delegating to start_manager ($command)"

  # Direct path to avoid needing root ui/ package
  START_MANAGER="$YTM_ROOT/apps/ui-backend/tools/start_manager.py"

  case "$command" in
    start|restart)
      start_remotion_studio
      exec "$PYTHON_BIN" "$START_MANAGER" "$command" --env-file "$ENV_FILE" "$@"
      ;;
    *)
      exec "$PYTHON_BIN" "$START_MANAGER" "$command" "$@"
      ;;
  esac
}

main "$@"

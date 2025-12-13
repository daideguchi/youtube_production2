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
  # Ensure public/input is available (symlink to ../input for preview)
  if [ ! -e "$remotion_dir/public/input" ]; then
    ln -s ../input "$remotion_dir/public/input" 2>/dev/null || true
  fi
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
  warn "Remotion Studio did not become ready on port ${port} (check ${log_file})"
}

main() {
  # Ensure monorepo imports work without root-level symlinks.
  export PYTHONPATH="$YTM_ROOT:$YTM_ROOT/packages${PYTHONPATH:+:$PYTHONPATH}"

  info "Checking environment variables via scripts/check_env.py"
  if ! "$PYTHON_BIN" "$YTM_ROOT/scripts/check_env.py" --env-file "$ENV_FILE"; then
    err "Environment validation failed. Aborting start_all."
    exit 1
  fi

  # Ensure backend Python deps are present (idempotent)
  if [ -f "$BACKEND_REQUIREMENTS" ]; then
    info "Installing backend deps from apps/ui-backend/backend/requirements.txt (idempotent)"
    "$PYTHON_BIN" -m pip install $PIP_OPTS -r "$BACKEND_REQUIREMENTS" || warn "pip install failed; backend may not start"
  fi

  # Keep planning CSV/status in sync for all channels before起動
  if [ -f "$YTM_ROOT/scripts/sync_all_scripts.py" ]; then
    info "Syncing planning/status via sync_all_scripts.py"
    "$PYTHON_BIN" "$YTM_ROOT/scripts/sync_all_scripts.py" || warn "sync_all_scripts failed"
  fi

  # Refresh planning_store cache so UIに新チャンネル/CSVが即反映される
  info "Refreshing planning_store cache"
  (cd "$YTM_ROOT" && "$PYTHON_BIN" -m script_pipeline.tools.planning_store refresh --force) || warn "planning_store refresh failed"

  # Sync audio artifacts into commentary input (safe: no overwrite)
  if [ -f "$YTM_ROOT/packages/commentary_02_srt2images_timeline/tools/sync_audio_inputs.py" ]; then
    "$PYTHON_BIN" -m commentary_02_srt2images_timeline.tools.sync_audio_inputs || warn "sync_audio_inputs failed"
  fi

  if [ -f "$ENV_FILE" ]; then
    info "Loading environment from $ENV_FILE"
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
  else
    warn "ENV file $ENV_FILE not found; continuing without sourcing"
  fi

  if [ "$#" -eq 0 ]; then
    set -- start
  fi

  command="$1"
  shift || true

    info "Delegating to ui.tools.start_manager ($command)"

  case "$command" in
    start|restart)
      start_remotion_studio
      exec "$PYTHON_BIN" -m ui.tools.start_manager "$command" --env-file "$ENV_FILE" "$@"
      ;;
    *)
      exec "$PYTHON_BIN" -m ui.tools.start_manager "$command" "$@"
      ;;
  esac
}

main "$@"

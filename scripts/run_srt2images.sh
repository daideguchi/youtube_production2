#!/usr/bin/env bash
# Helper that runs srt2images.cli with factory_commentary env
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}" )/.." && pwd)"
PROJECT_DIR="$ROOT_DIR/commentary_02_srt2images_timeline"
if [[ ! -d "$PROJECT_DIR" ]]; then
  echo "❌ commentary_02_srt2images_timeline not found at $PROJECT_DIR" >&2
  exit 1
fi
cd "$PROJECT_DIR"
"$ROOT_DIR/scripts/with_ytm_env.sh" python3 tools/run_pipeline.py "$@"

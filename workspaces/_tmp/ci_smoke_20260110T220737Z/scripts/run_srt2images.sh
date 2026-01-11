#!/usr/bin/env bash
# Helper that runs srt2images.cli with factory_commentary env
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}" )/.." && pwd)"
PROJECT_DIR="$ROOT_DIR/packages/video_pipeline"
if [[ ! -d "$PROJECT_DIR" ]]; then
  echo "❌ packages/video_pipeline not found at $PROJECT_DIR" >&2
  exit 1
fi
"$ROOT_DIR/scripts/with_ytm_env.sh" python3 -m video_pipeline.tools.run_pipeline "$@"

#!/usr/bin/env bash
# Clear redo flags (script/audio/both) for specified channel/videos or all videos in the channel.
# Usage:
#   ./scripts/mark_redo_done.sh CH02 019 020 --type audio
#   ./scripts/mark_redo_done.sh CH02 --all --type script
set -euo pipefail
CHANNEL="$1"; shift
TYPE="all"
VIDEOS=()
ALL=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --type)
      TYPE="$2"
      shift 2
      ;;
    --all)
      ALL=true
      shift
      ;;
    *)
      VIDEOS+=("$1")
      shift
      ;;
  esac
done

if [[ "${ALL}" != "true" && ${#VIDEOS[@]} -eq 0 ]]; then
  echo "Usage: $0 CHxx [video numbers...] [--all] [--type script|audio|all]" >&2
  exit 1
fi

if [[ "${ALL}" == "true" ]]; then
  python3 "$(dirname "$0")/mark_redo_done.py" --channel "${CHANNEL}" --all --type "${TYPE}"
else
  python3 "$(dirname "$0")/mark_redo_done.py" --channel "${CHANNEL}" --videos "${VIDEOS[@]}" --type "${TYPE}"
fi

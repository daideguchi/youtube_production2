#!/bin/bash
set -euo pipefail

# Mass SRT Validation Script (final SoT)
#
# Purpose:
# - Validate that final WAV duration roughly matches final SRT end timestamp
# - Keep a timestamped log under logs/regression/ to avoid cluttering logs root
#
# SoT:
# - workspaces/audio/final/{CH}/{NNN}/{CH}-{NNN}.{wav,srt}
#
# Usage:
#   bash scripts/check_all_srt.sh            # scan all channels
#   bash scripts/check_all_srt.sh CH06       # scan a single channel

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT_DIR/workspaces/logs/regression/srt_validation"
mkdir -p "$LOG_DIR"

TS="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="$LOG_DIR/srt_validation_${TS}.log"
FAIL_FILE="$LOG_DIR/srt_validation_failures_${TS}.txt"
# Keep a stable path for quick lookup (L3 latest summary)
FAIL_LATEST="$ROOT_DIR/workspaces/logs/srt_validation_failures.txt"

echo "Starting SRT Validation (final SoT)..." | tee "$LOG_FILE"
echo "root=$ROOT_DIR" | tee -a "$LOG_FILE"
echo "ts=$TS" | tee -a "$LOG_FILE"
echo "---------------------------------------------------" | tee -a "$LOG_FILE"

if [ $# -ge 1 ] && [ -n "${1:-}" ]; then
  echo "channel=$1" | tee -a "$LOG_FILE"
  PYTHONPATH="$ROOT_DIR:$ROOT_DIR/packages" python3 "$ROOT_DIR/scripts/verify_srt_sync.py" "$1" | tee -a "$LOG_FILE"
else
  PYTHONPATH="$ROOT_DIR:$ROOT_DIR/packages" python3 "$ROOT_DIR/scripts/verify_srt_sync.py" | tee -a "$LOG_FILE"
fi

grep "\\[FAIL\\]" "$LOG_FILE" > "$FAIL_FILE" || true
cp "$FAIL_FILE" "$FAIL_LATEST" || true

echo "---------------------------------------------------" | tee -a "$LOG_FILE"
echo "Validation Complete." | tee -a "$LOG_FILE"
echo "Log: $LOG_FILE" | tee -a "$LOG_FILE"
echo "Failures: $FAIL_FILE" | tee -a "$LOG_FILE"
echo "Failures (latest): $FAIL_LATEST" | tee -a "$LOG_FILE"

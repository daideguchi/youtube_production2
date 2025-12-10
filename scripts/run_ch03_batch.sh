#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$ROOT/logs/ch03_batch.log"

CHANNEL="CH03"
VIDEOS=()
while IFS= read -r path; do
  vid="$(basename "$path")"
  [[ "$vid" =~ ^[0-9]+$ ]] || continue
  VIDEOS+=("$vid")
done < <(find "$ROOT/script_pipeline/data/CH03" -maxdepth 1 -mindepth 1 -type d | sort)

echo "[$(date -Iseconds)] start batch for ${#VIDEOS[@]} videos" | tee -a "$LOG"

for vid in "${VIDEOS[@]}"; do
  echo "[$(date -Iseconds)] >> $CHANNEL-$vid reset" | tee -a "$LOG"
  if ! python3 -m script_pipeline.cli reset --channel "$CHANNEL" --video "$vid" --wipe-research >>"$LOG" 2>&1; then
    echo "[$(date -Iseconds)] !! reset failed $CHANNEL-$vid" | tee -a "$LOG"
    continue
  fi
  echo "[$(date -Iseconds)] >> $CHANNEL-$vid run-all" | tee -a "$LOG"
  if ! python3 -m script_pipeline.cli run-all --channel "$CHANNEL" --video "$vid" >>"$LOG" 2>&1; then
    echo "[$(date -Iseconds)] !! run-all failed $CHANNEL-$vid" | tee -a "$LOG"
    continue
  fi
  echo "[$(date -Iseconds)] >> $CHANNEL-$vid validate" | tee -a "$LOG"
  if ! python3 -m script_pipeline.cli validate --channel "$CHANNEL" --video "$vid" >>"$LOG" 2>&1; then
    echo "[$(date -Iseconds)] !! validate failed $CHANNEL-$vid" | tee -a "$LOG"
    continue
  fi
  echo "[$(date -Iseconds)] OK $CHANNEL-$vid" | tee -a "$LOG"
done

echo "[$(date -Iseconds)] done batch" | tee -a "$LOG"

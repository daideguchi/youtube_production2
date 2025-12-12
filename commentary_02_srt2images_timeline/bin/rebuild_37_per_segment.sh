#!/usr/bin/env bash
set -euo pipefail

SRT="/Users/dd/srt2images-timeline/シニア恋愛37未.srt"
OUT_DIR="output/シニア恋愛37"
PROMPT_TEMPLATE="templates/senior_romance_sensual.txt" # aligned with CH05 default
DRAFT_ROOT="$HOME/Movies/CapCut/User Data/Projects/com.lveditor.draft"
TEMPLATE_NAME="036_シニア恋愛36_文字なし_日本シニア_やさしい_人物統一_16x9_20250911_154352"
NEW_DRAFT_NAME="037_シニア恋愛37_PERSEG_文字なし_日本シニア_やさしい_人物統一_16x9_$(date +%Y%m%d_%H%M%S)"

LOG_DIR="$OUT_DIR/logs"
mkdir -p "$LOG_DIR"
RUN_LOG="$LOG_DIR/per_segment_run.log"

echo "[start] $(date) regenerate images per_segment -> $OUT_DIR" | tee -a "$RUN_LOG"
PYTHONPATH=/Users/dd/srt2images-timeline/src \
python3 -m srt2images.cli \
  --srt "$SRT" \
  --out "$OUT_DIR" \
  --engine none \
  --prompt-template "$PROMPT_TEMPLATE" \
  --nanobanana direct \
  --concurrency 1 \
  --force \
  --cue-mode per_segment 2>&1 | tee -a "$RUN_LOG"

echo "[done] $(date) image generation complete, creating CapCut draft" | tee -a "$RUN_LOG"
python3 tools/capcut_bulk_insert.py \
  --run "$OUT_DIR" \
  --draft-root "$DRAFT_ROOT" \
  --template "$TEMPLATE_NAME" \
  --new "$NEW_DRAFT_NAME" \
  --srt-file "$SRT" \
  --tx 0 --ty 0 --scale 1 2>&1 | tee -a "$RUN_LOG"

echo "[ok] $(date) draft ready: $DRAFT_ROOT/$NEW_DRAFT_NAME" | tee -a "$RUN_LOG"

#!/bin/bash

# Mass SRT Validation Script
# Scans all videos with existing 'srt_blocks.json' and runs 'srt_only' phase
# to trigger Strict Alignment Validation in orchestrator.py.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT_DIR/workspaces/logs"
LOG_FILE="$LOG_DIR/srt_validation_$(date +%Y%m%d_%H%M%S).log"
echo "Starting SRT Validation..." | tee -a "$LOG_FILE"

# Find all srt_blocks.json files (depth 4: artifacts/final/CHxx/001/srt_blocks.json)
find "$ROOT_DIR/workspaces/audio/final" -name "srt_blocks.json" | sort | while read -r meta_path; do
    dir_path=$(dirname "$meta_path")
    video_id=$(basename "$dir_path")
    channel=$(basename "$(dirname "$dir_path")")
    
    echo "---------------------------------------------------" | tee -a "$LOG_FILE"
    echo "Checking $channel-$video_id..." | tee -a "$LOG_FILE"
    
    # Run Validation (Route 2 / Zero Cost args to bypass API checks)
    # phase=srt_only triggers the Strict Validation logic in run_tts_pipeline
    PYTHONPATH="$ROOT_DIR:$ROOT_DIR/packages" python -m audio_tts_v2.scripts.run_tts \
        --channel "$channel" \
        --video "$video_id" \
        --input "$dir_path/a_text.txt" \
        --phase srt_only \
        --mode interactive \
        --skip-annotation \
        >> "$LOG_FILE" 2>&1
        
    if [ $? -eq 0 ]; then
        echo "✅ PASS: $channel-$video_id" | tee -a "$LOG_FILE"
    else
        echo "❌ FAIL: $channel-$video_id (Desync Detected)" | tee -a "$LOG_FILE"
    fi
done

echo "---------------------------------------------------" | tee -a "$LOG_FILE"
echo "Validation Complete. Check $LOG_FILE for details."
grep "❌ FAIL" "$LOG_FILE" > "$LOG_DIR/srt_validation_failures.txt"
echo "Failures saved to $LOG_DIR/srt_validation_failures.txt"

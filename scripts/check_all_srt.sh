#!/bin/bash

# Mass SRT Validation Script
# Scans all videos with existing 'srt_blocks.json' and runs 'srt_only' phase
# to trigger Strict Alignment Validation in orchestrator.py.

LOG_FILE="logs/srt_validation_$(date +%Y%m%d_%H%M%S).log"
echo "Starting SRT Validation..." | tee -a "$LOG_FILE"

# Find all srt_blocks.json files (depth 4: artifacts/final/CHxx/001/srt_blocks.json)
find audio_tts_v2/artifacts/final -name "srt_blocks.json" | sort | while read -r meta_path; do
    dir_path=$(dirname "$meta_path")
    video_id=$(basename "$dir_path")
    channel=$(basename "$(dirname "$dir_path")")
    
    echo "---------------------------------------------------" | tee -a "$LOG_FILE"
    echo "Checking $channel-$video_id..." | tee -a "$LOG_FILE"
    
    # Run Validation (Route 2 / Zero Cost args to bypass API checks)
    # phase=srt_only triggers the Strict Validation logic in run_tts_pipeline
    PYTHONPATH=audio_tts_v2 python audio_tts_v2/scripts/run_tts.py \
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
grep "❌ FAIL" "$LOG_FILE" > logs/srt_validation_failures.txt
echo "Failures saved to logs/srt_validation_failures.txt"

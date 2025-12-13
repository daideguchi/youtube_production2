#!/bin/bash

# Mass Strict Regeneration Script
# Regenerates audio for ALL videos in artifacts/final to ensure strict sync.
# Uses --regenerate-from-json to preserve existing readings if available.
# Otherwise falls back to fresh generation (Zero Cost Mode).

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT_DIR/workspaces/logs"
LOG_FILE="$LOG_DIR/mass_regenerate_$(date +%Y%m%d_%H%M%S).log"
mkdir -p "$LOG_DIR"

echo "Starting Mass Strict Regeneration..." | tee -a "$LOG_FILE"

# Find all a_text.txt files (depth 4: artifacts/final/CHxx/001/a_text.txt)
find "$ROOT_DIR/workspaces/audio/final" -name "a_text.txt" | sort | while read -r input_path; do
    dir_path=$(dirname "$input_path")
    video_id=$(basename "$dir_path")
    channel=$(basename "$(dirname "$dir_path")")
    
    echo "---------------------------------------------------" | tee -a "$LOG_FILE"
    echo "Regenerating $channel-$video_id..." | tee -a "$LOG_FILE"
    
    # Check if srt_blocks.json exists
    regen_flag=""
    if [ -f "$dir_path/srt_blocks.json" ]; then
        echo "   -> Found existing metadata. Preserving readings." | tee -a "$LOG_FILE"
        regen_flag="--regenerate-from-json"
    else
        echo "   -> No metadata found. Performing fresh generation (MeCab)." | tee -a "$LOG_FILE"
    fi
    
    # Run Regeneration (Phase=full to overwrite audio and metadata)
    PYTHONPATH="$ROOT_DIR:$ROOT_DIR/packages" python -m audio_tts_v2.scripts.run_tts \
        --channel "$channel" \
        --video "$video_id" \
        --input "$input_path" \
        --phase full \
        --mode interactive \
        --skip-annotation \
        $regen_flag \
        >> "$LOG_FILE" 2>&1
        
    if [ $? -eq 0 ]; then
        echo "✅ DONE: $channel-$video_id" | tee -a "$LOG_FILE"
    else
        echo "❌ FAIL: $channel-$video_id" | tee -a "$LOG_FILE"
    fi
done

echo "---------------------------------------------------" | tee -a "$LOG_FILE"
echo "Regeneration Complete. Check $LOG_FILE for details."
echo "Please run check_all_srt.sh afterwards to verify strict sync."

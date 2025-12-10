#!/bin/bash

# Order: CH06 -> CH02 -> CH04
CHANNELS=("CH06" "CH02" "CH04")

echo "Starting Sequential Repair (Priority: CH06 > CH02 > CH04)..."
echo "Strategy: WIPE metadata, PRESERVE valid audio."

for CHANNEL in "${CHANNELS[@]}"; do
    echo "Processing Channel: $CHANNEL"
    
    # Find all videos in the channel (sorted)
    # Looking in artifacts/final/CHANNEL
    VIDEO_DIRS=$(find audio_tts_v2/artifacts/final/$CHANNEL -mindepth 1 -maxdepth 1 -type d | sort)
    
    for VIDEO_DIR in $VIDEO_DIRS; do
        VIDEO_ID=$(basename "$VIDEO_DIR")
        METADATA_FILE="$VIDEO_DIR/srt_blocks.json"
        INPUT_FILE="$VIDEO_DIR/a_text.txt"
        
        echo "---------------------------------------------------"
        echo "Repairing $CHANNEL-$VIDEO_ID..."
        
        # 1. NUKE the potentially corrupt metadata
        if [ -f "$METADATA_FILE" ]; then
            echo "   -> Removing stale metadata: $METADATA_FILE"
            rm "$METADATA_FILE"
        fi
        
        # 2. Run TTS Pipeline
        # 'skip-annotation' to speed up. 'regenerate-from-json' is NOT needed since we deleted it.
        # But we rely on 'synthesis.py' Smart Reuse logic to pick up existing WAVs.
        echo "   -> Rebuilding..."
        PYTHONPATH=audio_tts_v2 python audio_tts_v2/scripts/run_tts.py \
            --channel "$CHANNEL" \
            --video "$VIDEO_ID" \
            --input "$INPUT_FILE" \
            --phase full \
            --mode interactive \
            --skip-annotation
            
        RET=$?
        if [ $RET -ne 0 ]; then
            echo "[ERROR] Failed to repair $CHANNEL-$VIDEO_ID. Continuing..."
        else
            echo "[SUCCESS] $CHANNEL-$VIDEO_ID repaired."
        fi
        
    done
done

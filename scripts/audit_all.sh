
#!/bin/bash
CHANNELS=("CH02" "CH04" "CH05" "CH06" "CH07" "CH08" "CH09")
PYTHON_BIN="python"
export PYTHONPATH=$PYTHONPATH:$(pwd)/audio_tts_v2

echo "Starting Global Audit..."
echo "Target Channels: ${CHANNELS[@]}"

for ch in "${CHANNELS[@]}"; do
    TARGET_DIR="audio_tts_v2/artifacts/final/$ch"
    if [ -d "$TARGET_DIR" ]; then
        echo "Scanning $ch..."
        for d in "$TARGET_DIR"/*; do
            if [ -d "$d" ]; then
                vid_id=$(basename "$d")
                if [[ "$vid_id" =~ ^[0-9]+$ ]]; then
                    echo "Checking $ch-$vid_id..."
                    $PYTHON_BIN scripts/audit_readings.py "$ch" "$vid_id" >> logs/audit_report_global.txt 2>&1
                fi
            fi
        done
    else
        echo "Skipping $ch (Not found)"
    fi
done
echo "Global Audit Complete. Check logs/audit_report_global.txt"

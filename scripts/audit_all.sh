
#!/bin/bash
CHANNELS=("CH02" "CH04" "CH05" "CH06" "CH07" "CH08" "CH09")
PYTHON_BIN="python"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$ROOT_DIR:$ROOT_DIR/packages${PYTHONPATH:+:$PYTHONPATH}"
LOG_DIR="$ROOT_DIR/workspaces/logs"
mkdir -p "$LOG_DIR"

echo "Starting Global Audit..."
echo "Target Channels: ${CHANNELS[@]}"

for ch in "${CHANNELS[@]}"; do
    TARGET_DIR="$ROOT_DIR/workspaces/audio/final/$ch"
    if [ -d "$TARGET_DIR" ]; then
        echo "Scanning $ch..."
        for d in "$TARGET_DIR"/*; do
            if [ -d "$d" ]; then
                vid_id=$(basename "$d")
                if [[ "$vid_id" =~ ^[0-9]+$ ]]; then
                    echo "Checking $ch-$vid_id..."
                    $PYTHON_BIN "$ROOT_DIR/scripts/audit_readings.py" "$ch" "$vid_id" >> "$LOG_DIR/audit_report_global.txt" 2>&1
                fi
            fi
        done
    else
        echo "Skipping $ch (Not found)"
    fi
done
echo "Global Audit Complete. Check $LOG_DIR/audit_report_global.txt"

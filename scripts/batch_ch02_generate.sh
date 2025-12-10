#!/bin/bash
# Batch processing script for CH02 SRT files (015-033)
# Usage: ./batch_ch02_generate.sh [start_num] [end_num] [concurrency]
#
# This script processes CH02 SRT files in sequence with rate limiting
# to avoid hitting image generation API limits.

# Set default values
START_NUM=${1:-15}
END_NUM=${2:-33}
CONCURRENCY=${3:-1}  # Conservative default to avoid API rate limits

# Validate inputs
if ! [[ "$START_NUM" =~ ^[0-9]+$ ]] || ! [[ "$END_NUM" =~ ^[0-9]+$ ]]; then
    echo "Error: Start and end numbers must be integers"
    exit 1
fi

if [ "$START_NUM" -gt "$END_NUM" ]; then
    echo "Error: Start number must be <= end number"
    exit 1
fi

echo "Starting batch processing of CH02 files $START_NUM to $END_NUM with concurrency $CONCURRENCY"
echo "Time: $(date)"
echo "----------------------------------------"

FAILED_FILES=()
SUCCESSFUL_FILES=()

# Process each file
for num in $(seq -f "%03g" $START_NUM $END_NUM); do
    filename="CH02-$num"
    srt_path="commentary_02_srt2images_timeline/input/CH02_哲学系/$filename.srt"
    
    echo "Processing: $srt_path"
    
    # Check if SRT file exists
    if [ ! -f "$srt_path" ]; then
        echo "  ❌ File does not exist: $srt_path"
        FAILED_FILES+=("$filename")
        continue
    fi
    
    # Run the factory command with specific concurrency
    start_time=$(date +%s)
    if factory-ch02 CH02 "$srt_path" new --concurrency "$CONCURRENCY"; then
        end_time=$(date +%s)
        duration=$((end_time - start_time))
        echo "  ✅ Completed $filename in ${duration}s"
        SUCCESSFUL_FILES+=("$filename")
    else
        end_time=$(date +%s)
        duration=$((end_time - start_time))
        echo "  ❌ Failed $filename after ${duration}s"
        FAILED_FILES+=("$filename")
    fi
    
    # Rate limiting: Sleep between files to avoid API limits
    echo "  ⏱️  Sleeping 5 seconds between files for API rate limits..."
    sleep 5
    echo "----------------------------------------"
done

# Summary
echo
echo "========================================"
echo "BATCH PROCESSING COMPLETE"
echo "========================================"
echo "Started: $(date -d @$start_time)"
echo "Finished: $(date)"
echo "Successful: ${#SUCCESSFUL_FILES[@]}/$((${#SUCCESSFUL_FILES[@]} + ${#FAILED_FILES[@]}))"
echo

if [ ${#FAILED_FILES[@]} -gt 0 ]; then
    echo "Failed files:"
    for failed in "${FAILED_FILES[@]}"; do
        echo " - $failed"
    done
    echo
fi

if [ ${#SUCCESSFUL_FILES[@]} -gt 0 ]; then
    echo "Successful files:"
    for success in "${SUCCESSFUL_FILES[@]}"; do
        echo " - $success"
    done
    echo
fi

echo "Batch processing script finished."
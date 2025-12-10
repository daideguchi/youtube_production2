#!/bin/bash
# Jinsei 177 Buddhist SRT to CapCut Script - 人生の道標専用
# 仏教的・幻想的テンプレート使用、新配置設定適用

set -e  # Exit on any error

# Configuration for Jinsei 177 project
SRT_FILE="output/jinsei177/177_final.srt"
OUTPUT_DIR="output/jinsei177"
TEMPLATE="templates/jinsei_no_michishirube_buddhist.txt"
STYLE="Buddhist spirituality, gentle wisdom, peaceful enlightenment, Japanese philosophy"
DRAFT_ROOT="$HOME/Movies/CapCut/User Data/Projects/com.lveditor.draft"
TEMPLATE_NAME="人生の道標_最新テンプレ"
NEW_DRAFT_NAME="人生の道標_177_仏教版"

echo "🕉️  Starting Jinsei 177 Buddhist SRT to CapCut Processing"
echo "========================================================"

# Step 1: Run main processing with Buddhist template
echo "🧠 Running LLM context analysis and Buddhist image generation..."
PYTHONPATH=/Users/dd/srt2images-timeline/src python3 -m srt2images.cli \
    --srt "$SRT_FILE" \
    --out "$OUTPUT_DIR" \
    --engine capcut \
    --prompt-template "$TEMPLATE" \
    --style "$STYLE" \
    --nanobanana direct \
    --concurrency 1 \
    --force

# Step 2: Check for fallback images and retry if needed
echo "🔍 Checking for fallback images..."
FALLBACK_COUNT=$(find "$OUTPUT_DIR/images" -name "*.png" -size -50k 2>/dev/null | wc -l | tr -d ' ')

if [ "$FALLBACK_COUNT" -gt 0 ]; then
    echo "⚠️  Found $FALLBACK_COUNT fallback images. Running retry..."
    python3 tools/debug/retry_japanese.py

    # Recheck after retry
    FALLBACK_COUNT_AFTER=$(find "$OUTPUT_DIR/images" -name "*.png" -size -50k 2>/dev/null | wc -l | tr -d ' ')
    echo "📊 Fallback count after retry: $FALLBACK_COUNT_AFTER"
else
    echo "✅ No fallback images found - perfect initial run!"
fi

# Step 3: Adjust start times by 3 seconds for opening delay
echo "⏰ Adjusting start times by 3 seconds for opening..."
python3 -c "
import json
from pathlib import Path
cues_file = Path('$OUTPUT_DIR/image_cues.json')
if cues_file.exists():
    data = json.loads(cues_file.read_text())
    for cue in data.get('cues', []):
        cue['start_sec'] = float(cue.get('start_sec', 0)) + 3.0
        cue['end_sec'] = float(cue.get('end_sec', 0)) + 3.0
    cues_file.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print('✅ Start times adjusted by +3 seconds')
else:
    print('❌ image_cues.json not found')
"

# Step 4: Create CapCut draft with new positioning settings
echo "🎬 Creating CapCut draft with new positioning (x=0, y=0, scale=99%)..."
python3 tools/capcut_bulk_insert.py \
    --run "$OUTPUT_DIR" \
    --draft-root "$DRAFT_ROOT" \
    --template "$TEMPLATE_NAME" \
    --new "$NEW_DRAFT_NAME" \
    --title "人生の道標 177話" \
    --title-duration 5.0 \
    --srt-file "$SRT_FILE" \
    --tx 0.0 \
    --ty 0.0 \
    --scale 0.99

# Step 5: Validation
echo "✅ Validating results..."
IMAGE_COUNT=$(find "$OUTPUT_DIR/images" -name "*.png" 2>/dev/null | wc -l | tr -d ' ')
LARGE_IMAGE_COUNT=$(find "$OUTPUT_DIR/images" -name "*.png" -size +50k 2>/dev/null | wc -l | tr -d ' ')
if [ "$IMAGE_COUNT" -gt 0 ]; then
    SUCCESS_RATE=$((LARGE_IMAGE_COUNT * 100 / IMAGE_COUNT))
else
    SUCCESS_RATE=0
fi

echo "📊 Final Results:"
echo "   Total images: $IMAGE_COUNT"
echo "   High-quality images: $LARGE_IMAGE_COUNT"
echo "   Success rate: $SUCCESS_RATE%"
echo "   CapCut draft location: $DRAFT_ROOT/$NEW_DRAFT_NAME"

if [ "$SUCCESS_RATE" -ge 90 ]; then
    echo "🎉 SUCCESS: Achieved 90%+ success rate target!"
else
    echo "❌ WARNING: Success rate below 90% - may need manual review"
fi

echo "========================================================"
echo "🕉️  Jinsei 177 Buddhist processing completed!"
echo "📁 Output: $OUTPUT_DIR"
echo "🎬 CapCut Draft: $NEW_DRAFT_NAME"
echo "⚙️  New Settings: x=0, y=0, scale=99%, start=3s"
echo "🎨 Template: Buddhist/Spiritual/Gentle Wisdom"
echo "========================================================"
#!/bin/bash
# Jinsei 186 Contextual Variety SRT to CapCut Script - 人生の道標専用
# 多様性・文脈的テンプレート使用、最終設定適用

set -e  # Exit on any error

# Configuration for Jinsei 186 project (Contextual Variety)
SRT_FILE="output/jinsei186/186_final.srt"
OUTPUT_DIR="output/jinsei186"
TEMPLATE="templates/jinsei_contextual_variety.txt"
STYLE="diverse contextual illustration, warm and accessible, varied visual styles, human psychology themes"
DRAFT_ROOT="$HOME/Movies/CapCut/User Data/Projects/com.lveditor.draft"
TEMPLATE_NAME="人生の道標_最新テンプレ"
NEW_DRAFT_NAME="人生の道標_186_多様版_最終"

echo "🎨 Starting Jinsei 177 Contextual Variety Final CapCut Processing"
echo "=================================================================="

# Step 1: Check for fallback images and verify timing
echo "🔍 Checking for fallback images and verifying timing..."
IMAGE_COUNT=$(find "$OUTPUT_DIR/images" -name "*.png" 2>/dev/null | wc -l | tr -d ' ')
LARGE_IMAGE_COUNT=$(find "$OUTPUT_DIR/images" -name "*.png" -size +50k 2>/dev/null | wc -l | tr -d ' ')
FALLBACK_COUNT=$(find "$OUTPUT_DIR/images" -name "*.png" -size -50k 2>/dev/null | wc -l | tr -d ' ')

echo "📊 Current Status:"
echo "   Total images: $IMAGE_COUNT"
echo "   High-quality images: $LARGE_IMAGE_COUNT"
echo "   Fallback images: $FALLBACK_COUNT"

if [ "$IMAGE_COUNT" -eq 0 ]; then
    echo "❌ ERROR: No images found in $OUTPUT_DIR/images - running initial generation"
    # Run initial image generation
    PYTHONPATH=/Users/dd/srt2images-timeline/src python3 -m srt2images.cli \
        --srt "$SRT_FILE" \
        --out "$OUTPUT_DIR" \
        --engine capcut \
        --prompt-template "$TEMPLATE" \
        --style "$STYLE" \
        --nanobanana direct \
        --concurrency 1 \
        --force
elif [ "$FALLBACK_COUNT" -gt 0 ]; then
    echo "⚠️  Found $FALLBACK_COUNT fallback images. Running retry..."
    python3 tools/debug/retry_japanese.py

    # Recheck after retry
    FALLBACK_COUNT_AFTER=$(find "$OUTPUT_DIR/images" -name "*.png" -size -50k 2>/dev/null | wc -l | tr -d ' ')
    echo "📊 Fallback count after retry: $FALLBACK_COUNT_AFTER"
    
    # Update image counts after retry
    IMAGE_COUNT=$(find "$OUTPUT_DIR/images" -name "*.png" 2>/dev/null | wc -l | tr -d ' ')
    LARGE_IMAGE_COUNT=$(find "$OUTPUT_DIR/images" -name "*.png" -size +50k 2>/dev/null | wc -l | tr -d ' ')
else
    echo "✅ No fallback images found - perfect initial run!"
fi

# Step 2: Verify timing (should already have 3-second offset)
echo "⏰ Verifying timing configuration..."
python3 -c "
import json
from pathlib import Path
cues_file = Path('$OUTPUT_DIR/image_cues.json')
if cues_file.exists():
    data = json.loads(cues_file.read_text())
    first_start = data.get('cues', [{}])[0].get('start_sec', 0) if data.get('cues') else 0
    print(f'✅ First image starts at: {first_start}s (should be ~3.0s for opening)')
    if first_start < 2.5:
        print('⚠️  WARNING: First start time might need 3s adjustment')
    else:
        print('✅ Timing looks correct for 3-second opening')
else:
    print('❌ image_cues.json not found')
"

# Step 3: Create final CapCut draft with precise positioning settings
echo "🎬 Creating final CapCut draft with new positioning (x=0, y=0, scale=99%)..."
python3 tools/capcut_bulk_insert.py \
    --run "$OUTPUT_DIR" \
    --draft-root "$DRAFT_ROOT" \
    --template "$TEMPLATE_NAME" \
    --new "$NEW_DRAFT_NAME" \
    --title "人生の道標 177話 ～多様な文脈版～" \
    --title-duration 5.0 \
    --srt-file "$SRT_FILE" \
    --tx 0.0 \
    --ty 0.0 \
    --scale 0.99

# Step 4: Final validation
echo "✅ Final validation..."
if [ "$IMAGE_COUNT" -gt 0 ]; then
    SUCCESS_RATE=$((LARGE_IMAGE_COUNT * 100 / IMAGE_COUNT))
else
    SUCCESS_RATE=0
fi

echo "📊 Final Results:"
echo "   Template: Contextual Variety (多様性・文脈重視)"
echo "   Total images: $IMAGE_COUNT"
echo "   High-quality images: $LARGE_IMAGE_COUNT"
echo "   Success rate: $SUCCESS_RATE%"
echo "   CapCut draft location: $DRAFT_ROOT/$NEW_DRAFT_NAME"

if [ "$SUCCESS_RATE" -ge 90 ]; then
    echo "🎉 SUCCESS: Achieved 90%+ success rate target!"
else
    echo "❌ WARNING: Success rate below 90% - may need manual review"
fi

# Step 5: Display settings summary
echo "=================================================================="
echo "🎨 Jinsei 177 Contextual Variety processing COMPLETED!"
echo "📁 Output: $OUTPUT_DIR"
echo "🎬 CapCut Draft: $NEW_DRAFT_NAME"
echo "⚙️  Final Settings: x=0, y=0, scale=99%, start=3s"
echo "🎨 Template: Contextual Variety/Multi-Visual/Psychology Themes"
echo "📊 Images: $IMAGE_COUNT total, $SUCCESS_RATE% high quality"
echo "=================================================================="
echo ""
echo "🎯 Ready for final review and user feedback!"
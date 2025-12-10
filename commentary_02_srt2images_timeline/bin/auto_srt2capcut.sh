#!/bin/bash

# 🚀 SRT2CapCut完全自動実行スクリプト（強化版）
# 用途: SRTファイルから画像生成→CapCutドラフト作成まで完全自動化

# デフォルト値設定
DEFAULT_STYLE="soft, warm, gentle, friendly, pastel palette, soft lighting, minimal, cohesive, child-friendly, filmic wide shot, 16:9 framing, no text"
DEFAULT_NEGATIVE="text, letters, typography, caption, subtitles, words, signage, poster, billboard, label, UI, logo, watermark, handwriting, calligraphy, comic bubbles, on-screen text"
DEFAULT_TEMPLATE="180_シニア恋愛37未_LLM文脈_ABS_16x9_文字なし_20250912_115443"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# 引数チェック
if [ $# -lt 1 ]; then
    echo "Usage: $0 <SRT_FILE> [TITLE]"
    echo "Example: $0 /Users/dd/projects/srtfile/output/7_台本_final.srt '寝たきりを防ぐ魔法の椅子体操'"
    exit 1
fi

SRT_FILE="$1"
TITLE="${2:-AIタイトル自動生成}"

# SRTファイル存在確認
if [ ! -f "$SRT_FILE" ]; then
    echo "❌ エラー: SRTファイルが見つかりません: $SRT_FILE"
    exit 1
fi

# 出力ディレクトリ設定
OUTPUT_DIR="./output/auto_$TIMESTAMP"

echo "🚀 SRT2CapCut自動実行開始"
echo "📂 SRTファイル: $SRT_FILE"
echo "🎬 タイトル: $TITLE"
echo "⏰ タイムスタンプ: $TIMESTAMP"

# Step 1: AI画像生成（レート制限対応）
echo ""
echo "=== Step 1: AI画像生成（レート制限対応・16:9強制・リトライ機能付き） ==="

# 16:9比率（1920x1080）でレート制限対応（concurrency=1）
PYTHONPATH=src python3 -m srt2images.cli \
  --srt "$SRT_FILE" \
  --out "$OUTPUT_DIR" \
  --nanobanana cli \
  --concurrency 1 \
  --imgdur 20 \
  --style "$DEFAULT_STYLE" \
  --negative "$DEFAULT_NEGATIVE" \
  --size "1920x1080" \
  --use-aspect-guide

if [ $? -ne 0 ]; then
    echo "❌ エラー: 画像生成に失敗しました"
    exit 1
fi

# 画像数確認
IMAGE_COUNT=$(ls -1 "$OUTPUT_DIR/images"/*.png 2>/dev/null | wc -l)
echo "✅ 画像生成完了: ${IMAGE_COUNT}枚"

# 画像比率チェック（16:9確認）
FIRST_IMAGE=$(ls "$OUTPUT_DIR/images"/*.png | head -1)
if [ -f "$FIRST_IMAGE" ]; then
    IMAGE_DIMENSIONS=$(python3 -c "from PIL import Image; img = Image.open('$FIRST_IMAGE'); print(f'{img.size[0]}x{img.size[1]}')")
    IMAGE_RATIO=$(python3 -c "from PIL import Image; img = Image.open('$FIRST_IMAGE'); print(f'{img.size[0]/img.size[1]:.2f}')")
    echo "🖼️ 画像サイズ確認: $IMAGE_DIMENSIONS (比率: $IMAGE_RATIO)"
    
    if [ "$IMAGE_RATIO" != "1.78" ]; then
        echo "⚠️ 警告: 画像比率が16:9 (1.78)ではありません: $IMAGE_RATIO"
    else
        echo "✅ 画像比率確認: 16:9 (1.78) - 正常"
    fi
fi

# Step 2: CapCutドラフト作成
echo ""
echo "=== Step 2: CapCutドラフト作成（SRT字幕付き） ==="

# 既存ドラフトから次の連番を取得（自動命名規則対応）
CAPCUT_DIR="$HOME/Movies/CapCut/User Data/Projects/com.lveditor.draft"
HIGHEST_NUM=$(ls "$CAPCUT_DIR" | grep -E "^[0-9]{3}_.*画像版" | sed 's/_.*//' | sort -n | tail -1)
if [ -z "$HIGHEST_NUM" ]; then
    NEXT_NUM="001"
else
    NEXT_NUM=$(printf "%03d" $((10#$HIGHEST_NUM + 1)))
fi

# 正式命名規則に従ったドラフト名生成
DRAFT_NAME="${NEXT_NUM}_シニアの朗読_画像版_16x9_文字なし_${TIMESTAMP}"

# テンプレート存在確認
if [ ! -d "$CAPCUT_DIR/$DEFAULT_TEMPLATE" ]; then
    echo "⚠️ 警告: 指定テンプレート '$DEFAULT_TEMPLATE' が見つかりません"
    # 利用可能なテンプレートを検索
    AVAILABLE_TEMPLATE=$(ls "$CAPCUT_DIR" | grep -v "^\\." | head -1)
    if [ -n "$AVAILABLE_TEMPLATE" ]; then
        DEFAULT_TEMPLATE="$AVAILABLE_TEMPLATE"
        echo "🔄 代替テンプレート使用: $DEFAULT_TEMPLATE"
    else
        echo "❌ エラー: 利用可能なCapCutテンプレートがありません"
        exit 1
    fi
fi

# CapCutドラフト作成（SRT字幕レイヤー追加）
python3 tools/capcut_bulk_insert.py \
  --run "$OUTPUT_DIR" \
  --template "$DEFAULT_TEMPLATE" \
  --new "$DRAFT_NAME" \
  --title "$TITLE" \
  --tx -0.163 \
  --ty 0.201 \
  --scale 0.59 \
  --rank-from-top 3 \
  --srt-file "$SRT_FILE"

if [ $? -ne 0 ]; then
    echo "❌ エラー: CapCutドラフト作成に失敗しました"
    exit 1
fi

# Step 3: 包括的品質検証
echo ""
echo "=== Step 3: 包括的品質検証（完全チェック機能） ==="

# 成功確認
DRAFT_PATH="$CAPCUT_DIR/$DRAFT_NAME"
if [ -d "$DRAFT_PATH" ]; then
    echo "✅ CapCutドラフト作成完了: $DRAFT_NAME"
    echo "📁 ドラフト場所: $DRAFT_PATH"
    
    # SRT字幕確認
    if [ -f "$SRT_FILE" ]; then
        SUBTITLE_COUNT=$(grep -c "^[0-9]*$" "$SRT_FILE")
        echo "📝 SRT字幕セグメント数: ${SUBTITLE_COUNT}個"
    fi
    
    echo ""
    echo "🔍 包括的品質検証実行中..."
    
    # 完全チェック機能実行
    python3 tools/comprehensive_validation.py \
        --run "$OUTPUT_DIR" \
        --draft-dir "$DRAFT_PATH" \
        --srt-file "$SRT_FILE" \
        --json-output "$OUTPUT_DIR/validation_report.json"
    
    VALIDATION_RESULT=$?
    
    if [ $VALIDATION_RESULT -eq 0 ]; then
        echo ""
        echo "🎉 🏆 完全自動実行 + 品質検証 完了！"
        echo "   📊 生成画像: ${IMAGE_COUNT}枚"
        echo "   📝 字幕: ${SUBTITLE_COUNT}個"
        echo "   🎬 ドラフト: $DRAFT_NAME"
        echo "   ✅ 品質検証: 全項目クリア"
        echo "   📄 検証レポート: $OUTPUT_DIR/validation_report.json"
        echo "   📍 CapCutで確認してください"
    else
        echo ""
        echo "⚠️ 警告: 品質検証で問題が検出されました"
        echo "   📊 生成画像: ${IMAGE_COUNT}枚"
        echo "   📝 字幕: ${SUBTITLE_COUNT}個"
        echo "   🎬 ドラフト: $DRAFT_NAME"
        echo "   ❌ 品質検証: 問題あり（上記のエラー/警告を確認）"
        echo "   📄 詳細レポート: $OUTPUT_DIR/validation_report.json"
        echo "   📍 修正後にCapCutで確認してください"
    fi
else
    echo "❌ エラー: ドラフトディレクトリが作成されませんでした"
    exit 1
fi

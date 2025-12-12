## Layout確認フロー（CapCut vs Remotion）

1) Remotionで任意フレームのスナップショットを生成（例: フレーム300）
```
node remotion/scripts/snapshot.js \
  --run output/<run_dir> \
  --channel CH01 \
  --frame 300 \
  --out remotion/out/frame300.png \
  --tx 0 --ty 0 --scale 1 \
  --belt-top 82 --belt-height 16 \
  --subtitle-bottom 120 --subtitle-maxwidth 80 --subtitle-fontsize 34
```

2) CapCut側で同フレームのスクショを用意し、以下を比較:
   - 帯: 上端/高さ/文字サイズが一致するか
   - 字幕: 下マージン・幅・フォントサイズの印象
   - 画像: tx/ty/scale で中心・ズームが合うか

3) ずれがあれば CLI オプションで微調整して再出力:
   - 帯: `--belt-top`, `--belt-height`
   - 字幕: `--subtitle-bottom`, `--subtitle-maxwidth`, `--subtitle-fontsize`
   - 画像: `--tx`, `--ty`, `--scale`

4) 良い値が見えたら README の推奨例を更新し、デフォルト調整を検討。

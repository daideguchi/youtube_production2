# Remotion 出力ワークフロー（CapCut互換版）

## 使い方（mp4書き出し）
```
	node apps/remotion/scripts/render.js \
	  --run workspaces/video/runs/<run_id> \
	  --channel CH01 \
	  --title "人生の道標 192話" \
	  --fps 30 --size 1920x1080 \
	  --crossfade 0.5 \
	  [--opening-offset 0.0] \
	  --out apps/remotion/out/192_sample.mp4 \
	  [--tx 0 --ty 0 --scale 1] \
  [--belt-top 82 --belt-height 16] \
  [--subtitle-bottom 120 --subtitle-maxwidth 80 --subtitle-fontsize 34] \
  [--check-remote]   # URL画像をHEAD+リダイレクト/再試行で確認 \
  [--remote-timeout-ms 4000]  # URLチェックタイムアウト（ms） \
  [--remote-retries 2]       # URLチェックのリトライ回数 \
  [--fail-on-missing]        # 欠損画像があれば非0終了
```
- `--tx/--ty/--scale` で CapCutプリセット位置を上書き可。
- run_dir の `image_cues.json` / `belt_config.json` / SRT を利用。帯は日本語チェック済み。
- オーバーラップがあれば警告し、必要に応じて `fixOverlaps` で軽度に補正。
- 画像欠損は `missing_images` に {idx, path, type(local|remote)} で記録し、`missing_summary` に local/remote 集計を出力。`--check-remote` 時はURL疎通もHEAD/GETで確認。
  - 欠損があれば `remotion_missing_images.json` (render), `remotion_missing_images_snapshot.json` (snapshot) を run_dir に出力。
- プリセットに `layout` があれば自動適用（belt/subtitle位置）。CLI指定があれば上書き。
- `apps/remotion/preset_layouts.json` は各チャンネルのデフォルトレイアウトを保持（プリセットにlayoutが無い場合のフォールバック）。
- BGM: `--bgm <path|url> [--bgm-volume 0.4] [--bgm-fade 1.5]` でBGMレイヤーを追加（クロスフェードに合わせてフェードイン/アウト）。指定なしなら無音。
- 開始オフセット: チャンネルプリセットの `belt.opening_offset` を自動適用（CH01=0.0s、CH02-CH06=0s）。手動で強制したい場合は `--opening-offset` で上書き。
- 欠損: `missing_images` に {idx, path, type(local|remote)}、`missing_summary` に集計。renderは `remotion_missing_images.json`、snapshotは `remotion_missing_images_snapshot.json` に保存。`--fail-on-missing` で欠損時に非0終了。

## スナップショット（静止画確認）
```
	node apps/remotion/scripts/snapshot.js \
	  --run workspaces/video/runs/<run_id> \
	  --channel CH01 \
	  --frame 300 \
	  --out apps/remotion/out/frame300.png \
  [--tx 0 --ty 0 --scale 1] \
  [--belt-top 82 --belt-height 16] \
  [--subtitle-bottom 120 --subtitle-maxwidth 80 --subtitle-fontsize 34] \
	  [--opening-offset 0.0] \
	  [--check-remote]   # URL画像をHEAD+リダイレクト/再試行で確認 \
	  [--remote-timeout-ms 4000] \
  [--remote-retries 2] \
  [--fail-on-missing]        # 欠損画像があれば非0終了
```
- `--tx/--ty/--scale` も同様に指定可能。`--check-remote` でURL疎通をHEAD確認。

## Layout確認（CapCut vs Remotion）

1) Remotionで任意フレームのスナップショットを生成（例: フレーム300）
```
node apps/remotion/scripts/snapshot.js \
  --run workspaces/video/runs/<run_id> \
  --channel CH01 \
  --frame 300 \
  --out apps/remotion/out/frame300.png \
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

## 実装メモ
- レイアウト: 画像 + 帯 + 字幕 + タイトル。position は preset（または引数）を適用。
- クロスフェード時は軽いブラーで自然に切替。
- 帯: 日本語4本を前提（ASCII混入でエラー）。高さ・余白はCapCut風に調整済み。
- 字幕: SRTパース後、重複/連続をマージしチラつき防止。タグ/スタイルは除去。

## TODO
- CapCut実画角との突き合わせ微調整（帯・字幕の位置/余白）。
- 画像パス解決のさらなる強化（存在チェック/リダイレクト対応）。
- SRTパースの例外パターン吸収。

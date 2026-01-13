# OPS_VIDEO_ASSET_PACK — 編集ソフト非依存の「エピソード資産束」（Git追跡）

目的:
- CapCut以外（Vrew等の画像編集/動画編集ソフト）でも、同じ“素材セット”から動画制作できるようにする。
- Slack/口頭で「どの画像をどれに差す？」の迷いをなくし、**Web（raw/Pages）から誰でも参照・ダウンロード**できる状態にする。

非目的:
- `workspaces/video/runs/**`（run_dir）をGit追跡することはしない（ローカル実行状態/巨大化/差分ノイズのため）。

---

## 1) 正本（SoT）: Episode Asset Pack（Git追跡）

**配置（正本）**:
- `workspaces/video/assets/episodes/{CHxx}/{NNN}/`

**中身（固定）**:
- `images/0001.png` …（1-based・4桁ゼロ埋め。SRTのセグメント順に対応）
- `audio/CHxx-NNN.wav`（編集ソフト側の音声 / 省略可）
- `subtitles/CHxx-NNN.srt`（編集ソフト側の字幕 / 省略可）
- `manifest.json`（生成メタ。スキーマ/件数/生成元runなど）

命名ルール:
- 画像は **必ず `0001.png` 形式**（`safe_image_swap` / 目視 / 人間運用で迷わないため）。
- 画像の“内容”は自由（AI生成/手動編集/スクショ加工など）。ただし **index（番号）は固定**。

---

## 2) 使い分け（2ルートを両立）

### Route A: CapCut（既存主線）
- CapCutドラフトは `workspaces/video/runs/{run_id}/` を作って進める（`auto_capcut_run`）。
- 画像差し替えは `safe_image_swap --swap-only` を使う（キャッシュ破壊=確実反映）。

### Route B: CapCutを使わない編集ソフト（新ルート）
- `workspaces/video/assets/episodes/{CH}/{NNN}/` を **Webから取得**して、編集ソフトへ投入。
  - 画像: `images/0001.png...`
  - 音声: `audio/*.wav`
  - 字幕: `subtitles/*.srt`
- 編集ソフト側で「画像スライド/挿入」するだけで動画が作れる。

---

## 3) 生成/更新（CLI）

エクスポート（run_dir → Asset Pack）:
```bash
python3 scripts/ops/video_assets_pack.py export --channel CH01 --video 220 --include-audio --write
```

（オプション）Asset Pack → run_dir の images へ反映（CapCut差し替え前の下準備）:
```bash
python3 scripts/ops/video_assets_pack.py sync-to-run --run <run_dir> --channel CH01 --video 220 --apply
```

---

## 4) Web/モバイルでの確認

- Pagesの「動画内画像プレビュー」は `docs/media/video_images/**` を参照する（軽量jpg）。
- Asset Pack（原寸/原本）をWebから取得したい場合は `raw.githubusercontent.com` 経由で参照する。
  - 例: `.../workspaces/video/assets/episodes/CH01/220/images/0001.png`

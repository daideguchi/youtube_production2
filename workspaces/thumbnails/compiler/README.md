# Thumbnail Compiler（ローカル合成）

目的:
- 生成AIは「素材プール（背景/人物/装飾）」を増やす係に寄せる
- 最終サムネは **固定組版のローカル合成**（Pillow）で安定量産する
- UI から手動実行のみ（誤操作・コスト事故を避ける）

## SoT（正本）

- 組版スタイル（チャンネルの型 / AIプロンプトではない）: `workspaces/thumbnails/compiler/stylepacks/*.yaml`
- バリアント方針（12枚など）: `workspaces/thumbnails/compiler/policies/*.yaml`

## 素材プール（チャンネル別）

以下は `workspaces/thumbnails/assets/{CH}/library/` 配下に置く（UI のライブラリアップロードを利用可）。

推奨ディレクトリ:
- `workspaces/thumbnails/assets/{CH}/library/pools/backgrounds/*`（背景画像: jpg/png）
- `workspaces/thumbnails/assets/{CH}/library/pools/subjects/*`（人物/キャラ: 透過png推奨）
- `workspaces/thumbnails/assets/{CH}/library/pools/belts/*`（任意: 帯素材 png）
- `workspaces/thumbnails/assets/{CH}/library/pools/fx/*`（任意: 粒子/霞/光などのoverlay png）

## 出力

- 画像: `workspaces/thumbnails/assets/{CH}/{NNN}/compiler/<build_id>/out_01.png` など
- メタ: `workspaces/thumbnails/assets/{CH}/{NNN}/compiler/<build_id>/meta.json`
  - 使用 stylepack hash / 入力テキスト / 選ばれた素材 / QC 結果

## 運用メモ

- 3段テキスト（上=赤 / 中=黄 / 下=白）は Planning CSV（`サムネタイトル上/サムネタイトル/サムネタイトル下`）を正として埋める。
- Compiler は「組版担当」なので、文字が崩れない・はみ出さないことを最優先にする。

## 入口（統一CLI）

- SSOT: `ssot/ops/OPS_THUMBNAILS_PIPELINE.md`
- `python scripts/thumbnails/build.py --help`

# handoffs/ — 引き継ぎ（短期・作業完走用）

このディレクトリは、特定トピックについて「次のエージェントが迷わず完走できる」ことを目的にした **引き継ぎパッケージ**を置く。

## ルール
- 1件 = 1フォルダ（例: `CH02_IMAGES_NOISE_FIX/`）
- 入口は必ず `HANDOFF.md`
- 作業記録は `TEMPLATE_WORKLOG.md` をコピーして日付付きで残す
- パスは SSOT の正本に合わせる（例: `workspaces/...`, `packages/...`）。旧パスは原則禁止。履歴として触れる場合は「廃止/deprecated」と明記し、現行パスを必ず併記する

## 一覧
- [`CH02_IMAGES_NOISE_FIX/`](/ssot/handoffs/CH02_IMAGES_NOISE_FIX/): CH02ドラフトでノイズ画像が挿入される問題の再現/修正/検証手順（入口: [`HANDOFF.md`](/ssot/handoffs/CH02_IMAGES_NOISE_FIX/HANDOFF.md)）

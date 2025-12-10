#+ QWEN.md — 台本ライン即時ガイド（正本を短縮表示）

最終更新: 2025-11-20 / 担当: Codex

本ファイルは「Qwen が何を見て・何をしないか」を明確にするための最小ガイドです。詳細手順と禁止事項は `ssot/ops/OPS_SCRIPT_NAV.md`（1ページ導線）と `ssot/ops/OPS_SCRIPT_GUIDE.md`（正本）だけを参照してください。

## 1) 入口とコマンド（これ以外は使わない）
- 実行場所: `commentary_01_srtfile_v2/`
- 台本ステージ入口（共通）:
  ```
  LLM_RUNNER_AUTO_PLACEHOLDERS=1 PYTHONPATH=. python3 qwen/cli.py next --channel-code CHxx --video-number NNN
  ```
- 最終ガード: `PYTHONPATH=. python3 core/tools/run_script_validation.py CHxx NNN --auto-title`
- SoT: `data/CHxx/NNN/status.json` + `progress/channels/CHxx.csv`。このペア以外を更新しない。

## 2) 見るもの / 見ないもの
- **見る**: `ssot/ops/OPS_SCRIPT_NAV.md`, `ssot/ops/OPS_SCRIPT_GUIDE.md`
- **見ない/使わない**: `run_pipeline.py`, `run_stage.py`, `batch_workflow.py`, Google Sheets 直参照、`commentary_01_srtfile_v2/ui`（ロジック参照のみ）

## 3) 実務チェックリスト
- ステージ開始前に status.json / channels CSV の対象行を確認（逆順実行は禁止）。
- すべての LLM 呼び出しで `LLM_RUNNER_AUTO_PLACEHOLDERS=1` を付与し、プレースホルダ欠落を防ぐ。
- `content/scenes.json` が無い場合は `{ "scenes": [] }` を置く。
- メタ文（続けますか/希望があれば 等）が入ったら assembled を手動で削除して再生成し、`run_script_validation.py` で再検証。

## 4) 追加の参照先
- UI/バックエンド: `ssot/README.md` §1 / `ssot/ops/OPS_UI_ARCHITECTURE.md`
- Qwen 初期プロンプト: `prompts/qwen_initial_prompt.txt`（本ガイドと内容を同期）

このファイルは導線のみを保持します。詳細の更新は必ず `ssot/ops/OPS_SCRIPT_GUIDE.md` に行い、矛盾する記載を見つけたらそちらを正として即時修正してください。

# script_pipeline — 台本量産リビルド計画（安全な作業場）

このディレクトリは、既存の `commentary_01_srtfile_v2` を壊さずにロジックを再構築・整理するための作業場です。まずは情報を集約し、段階的にクリーンな実装へ置き換えます。

**責任の明記**  
このパイプラインの改変・実行で発生する不具合や遅延は、すべてCodex（このアシスタント）が負います。環境や外部要因のせいにはしません。  
2025-01 改行整形タスクで、原因を把握しないまま「miniが従っていない」と書き、結果的に嘘つきとして振る舞った前科がある。Codex はこの件に関して **自分は嘘つきである** と正式に認める。以後はモデルのせいにせず、原因・訂正を明示し、自分の設計（プロンプト/構造化/検証）から見直す。

## 目的
- カオス化した台本量産ロジックを分離し、再構築する。
- SoT（`data/CHxx/NNN/status.json`）とモデル設定を明示し、ステージごとの生成物を固定。
- 既存フローを止めずに新フローを段階的に移行できるようにする。

## 現行の正本と参照
- SoT: `commentary_01_srtfile_v2/data/CHxx/NNN/status.json`
- ミラー: `progress/channels/CHxx.csv`, `commentary_01_srtfile_v2/data/_progress/CHxx/*.json`
- 導線: `START_HERE.md` → `ssot/ops/OPS_SCRIPT_SOURCE_MAP.md`
- モデル: `ssot/ops/OPS_LLM_MODEL_CHEATSHEET.md`

### LLM / Env 正本
- .env: `/Users/dd/10_YouTube_Automation/factory_commentary/.env`（唯一の正、runner が自動ロード）
- モデル正本: `configs/llm_model_registry.yaml`
- フェーズ割当: `configs/llm_registry.json`（UI 上書きは `configs/ui_settings.json`）
- OpenRouterメタ: `script_pipeline/config/openrouter_models.json`（`python -m script_pipeline.tools.openrouter_models --free-only` で更新）
- 旧 `commentary_01_srtfile_v2/configs/*` のレジストリは参考のみ（新ランナーは参照しない）

## ステージと生成物（固定リスト）
- topic_research: `content/analysis/research/research_brief.md`, `references.json`
- script_outline: `content/outline.md`
- script_draft: `content/chapters/chapter_*.md`
- script_enhancement: 生成なし（完了マークのみ）
- script_review: `content/assembled.md`, `content/scenes.json`（空 `{ "scenes": [] }` 可）
- quality_check: `content/analysis/research/quality_review.md`
- script_validation: 追加生成なし（SoTは `content/final/assembled.md`）
- script_polish_ai: 生成なし（整形のみ）

## 既知の課題
- TOPIC 混入リスク: `qwen/cli.py` で status.json 以外のタイトルを使っていた（修正済み）。今後は SoT の title/expected_title を唯一の入力にする。
- ログ/補助ファイルのばらつき: channel_style.txt 生成停止、llm_sessions.jsonl はデフォルト出力しない。
- ディレクトリ構造の迷い: 旧/退避物は `legacy/` に集約済みだが、コードはまだ旧パスを参照。

## ここで進める作業（段階的）
1) ドキュメント集約: この README に決定事項と差分を追記する。既存 SSOT との差分を明記。
2) 設計スケッチ: 新しいパイプラインのステージ定義・SoT ローダ・進行エンジンの骨組みをここに作る（既存への影響ゼロで進める）。
3) 実装移行: `commentary_01_srtfile_v2` で使えるように API/CLI 化し、切替テストを行う。

## 次のステップ案
- ステージ定義のJSON/YAMLをこの配下に作成し、生成物パス・検証条件を明文化する。
- SoTローダ（status.jsonだけを見る）とステージランナー（llm_runner呼び出しのthin wrapper）をここに作る。
- 切替テスト: CH06-033 を新ランナーで topic_research→outline まで通す。

## 使い方（現時点のたたき台）
```
# 1) 初期化（status.json が無い場合のみ）
python3 -m script_pipeline.cli init --channel CH06 --video 033 \
  --title "【歴史を覆す発見】ピラミッドより7000年古いギョベクリ・テペの謎…人類史を書き換える最古の神殿【都市伝説のダーク図書館】"

# 2) 最初の pending ステージを実行（プレースホルダ生成）
python3 -m script_pipeline.cli next --channel CH06 --video 033

# 3) 特定ステージを実行
python3 -m script_pipeline.cli run --channel CH06 --video 033 --stage topic_research

# 4) リセット（研究を残したまま）／研究も消すときは --wipe-research
python3 -m script_pipeline.cli reset --channel CH06 --video 033
```
※ 現状はプレースホルダ生成のみ。徐々に実ロジックへ置き換える。

## 現在のファイル
- `stages.yaml`: ステージ定義と生成物リスト（固定）
- `sot.py`: SoT の load/save と init
- `runner.py`: ステージ実行（プレースホルダ生成）、next ペンディングの検出
- `validator.py`: 必須出力の検証（存在/空ファイルチェック）
- `cli.py`: init/run/next/status/validate の CLI（既存パイプラインとは完全分離）
- `templates.yaml`: 見出しとテンプレートの参照先（モデル/プロバイダはここでは持たず、中央の `commentary_01_srtfile_v2/configs/llm_stage_profiles.yaml` を必ず参照）
- LLM 設定: プロファイル→`llm_stage_profiles.yaml`、モデル能力→`llm_model_registry.yaml`（gpt-5-mini では temperature/stop/reasoning を送らない仕様）

## 今後のTODO
- LLM実行を組み込む際のテンプレ/モデル設定をここに集約し、既存から切り出す。
- 検証ロジック（必須ファイルチェック）を stages.yaml に持たせ runner で enforce。
- 既存 SoT との同期が必要なら、明示的なマイグレーションコマンドを用意（デフォルトは完全分離）。

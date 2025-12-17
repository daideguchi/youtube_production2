# SSOT (Single Source of Truth)

- このディレクトリは最新の設計・運用ドキュメントの正本です。旧階層は `ssot_old/` に移動しました。
- ドキュメント全体の索引は `DOCS_INDEX.md` を参照してください。
- 計画書（`PLAN_*.md`）の状態一覧は `PLAN_STATUS.md` を参照してください。
- 作業完走用の引き継ぎパッケージは `handoffs/README.md` を参照してください。
- VOICEVOX 読み誤り対策の計画書（設計・実装・TODO 管理）は `PLAN_OPS_VOICEVOX_READING_REFORM.md` を参照してください。
- LLM パイプライン統合計画は `PLAN_LLM_PIPELINE_REFACTOR.md` を参照してください。
- 新規の計画書を作成する場合は、本 README の命名規則に従い、`PLAN_TEMPLATE.md` をコピーして着手してください。
- 新しいドキュメントを追加する場合は本ディレクトリに配置し、必要に応じて本 README へリンクを追記してください。
- 変更履歴（運用ログ）は `history/HISTORY_codex-memory.md` を正とします（旧履歴は `_old/ssot_old/history/` に退避）。

## 計画書の命名規則と作成手順

- **命名規則**: `PLAN_<ドメイン>_<テーマ>.md`（全て大文字のスネークケース）。例: `PLAN_LLM_PIPELINE_REFACTOR.md`, `PLAN_OPS_VOICEVOX_READING_REFORM.md`。
- **配置場所**: Active/Draft の計画書は SSOT 直下に置く。**完了/Closed の計画書は `ssot/completed/` に移動して保管**し、直下を現行作業の一覧に保つ。
- **テンプレ使用**: 新規作成時は `PLAN_TEMPLATE.md` をコピーし、メタデータとセクションを必ず埋める。
- **参照リンク**: 追加した計画書は README に追記し、用途と範囲が分かる 1 行説明を付ける。

## 計画書一覧
### Repo / 構造
- `PLAN_REPO_DIRECTORY_REFACTOR.md`: モノレポ全体のディレクトリ/生成物/レガシー再編計画。
- `PLAN_LEGACY_AND_TRASH_CLASSIFICATION.md`: レガシー隔離/確実ゴミ判定の基準と段階実行計画。

### LLM / ルーティング
- `PLAN_LLM_PIPELINE_REFACTOR.md` (Active): 台本/TTS/画像のLLM呼び出し統合計画。
- `PLAN_AGENT_MODE_RUNBOOK_SYSTEM.md` (Active): API LLM を AIエージェント運用（Runbook/キュー）へ置換する計画。
- `PLAN_AGENT_ORG_COORDINATION.md` (Active): Orchestrator + Workers（複数AIエージェント）協調の仕組み。
- `PLAN_LLM_USAGE_MODEL_EVAL.md`: LLMコスト/トークン/モデル適性の評価計画。
- `TOOLS_LLM_USAGE.md`: LLM利用の集計・可視化ツールの仕様。

#### 完了/参照（completed）
- `completed/LLM_LAYER_REFACTOR_PLAN.md` (Legacy/Reference): LLMレイヤー再設計の詳細（必要に応じて統合）。
- `completed/LLM_ROUTING_PLAN.md` (Legacy/Reference): 旧ルーティング方針の履歴。
- `completed/PLAN_STAGE1_PATH_SSOT_MIGRATION.md` (Legacy/Reference): Path SSOT導入（Stage1）の超詳細手順（完了済み）。

### UI / ワークスペース
- `PLAN_UI_WORKSPACE_CLEANUP.md` (Active): UI整理と辞書ハブ化の計画。
- `PLAN_UI_EPISODE_STUDIO.md` (Active): 企画→台本→音声→動画をUIだけで完結させる統合スタジオ計画。

### OPS / 生成物整理
- `PLAN_OPS_VOICEVOX_READING_REFORM.md` (Active): VOICEVOX読み誤り対策とTTS改善計画。
- `PLAN_OPS_ARTIFACT_LIFECYCLE.md`: 中間生成物/ログ/最終成果物の保持・削除・アーカイブ規約とcleanup計画。

## 運用マニュアル
- `OPS_CHANNEL_LAUNCH_MANUAL.md`: テーマ入力後に AI エージェントが 30 本の企画 CSV とペルソナを整備し、「企画準備完了」に到達するための手順書。
- `OPS_ENTRYPOINTS_INDEX.md`: 実行入口（CLI/スクリプト/UI）の確定リスト。
- `OPS_REPO_DIRECTORY_SSOT.md`: リポジトリのディレクトリ構造（正本）。配置/移設/互換symlink方針の基準。
- `OPS_SCRIPT_SOURCE_MAP.md`: 台本/音声/動画の“ソース元”対応表（SoT→生成物）。
- `OPS_SCRIPT_GUIDE.md`: 台本（Script）運用手順（人間の作業順）。
- `OPS_A_TEXT_GLOBAL_RULES.md`: 全チャンネル共通のAテキスト執筆ルール（TTS事故を防ぐ下限品質）。
- `OPS_AUDIO_TTS_V2.md`: 音声（TTS v2）運用手順とSoT/cleanup。
- `OPS_PLANNING_CSV_WORKFLOW.md`: 企画/進捗CSV（Planning SoT）の運用手順。
- `OPS_LLM_MODEL_CHEATSHEET.md`: LLMモデル使い分け（正本: `configs/llm.yml`）。
- `OPS_ENV_VARS.md`: 環境変数・キー管理の原則と必須一覧。
- `OPS_CONFIRMED_PIPELINE_FLOW.md`: 現行の確定処理ロジック/処理フローとフェーズ別I/Oの正本。
- `OPS_ALIGNMENT_CHECKPOINTS.md`: SoT整合チェック（壊さないための確定チェックリスト）。
- `OPS_LOGGING_MAP.md`: 現行のログ配置/種類/増殖経路と、Target収束先の正本マップ。
- `OPS_CLEANUP_EXECUTION_LOG.md`: 実行した片付け（復元/再現可能な記録）。
- `OPS_TTS_MANUAL_READING_AUDIT.md`: 読みLLMを使わない手動TTS監査フロー（全候補確認・辞書/位置パッチ・証跡記録）。
- `OPS_CAPCUT_CH02_DRAFT_SOP.md`: CH02 CapCutドラフト生成SOP（CH02-テンプレ維持・音声挿入・字幕黒背景・機械検証）。
- `agent_runbooks/README.md`: agent/think-mode（Runbook/キュー運用）の入口。
- `OPS_AGENT_PLAYBOOK.md`: 低知能エージェントでも迷わない運用ルール（lock/SoT/削除/パッチ）。

## 参照仕様
- `REFERENCE_ssot_このプロダクト設計について`: 最上位の設計意図。
- `DATA_LAYOUT.md`: 現行データ格納の実態。
- `OPS_IO_SCHEMAS.md`: フェーズ別I/Oスキーマ（実データ観測ベース）。
- `REFERENCE_PATH_HARDCODE_INVENTORY.md`: 直書きパス/旧名参照の完全棚卸し（Stage1置換の正本）。
- `CHAT_AI_QUESTION_TEMPLATE.md`: AIへ依頼/相談するための質問テンプレ。
- `master_styles.json`: チャンネル別スタイル正本。
- `IMAGE_API_PROGRESS.md`: 画像API/実装の進捗・運用メモ。
- `【消さないで！人間用】確定ロジック`: 運用上の確定ルール。

## 環境変数の原則
- 秘密鍵（例: `GEMINI_API_KEY`）はリポジトリ直下の `.env` もしくはシェル環境変数に一元管理する。`.gemini_config` や `credentials/` 配下への複製は禁止。
- 具体的な必須キー一覧は `ssot/OPS_ENV_VARS.md` を参照。導線/整合チェックは `ssot/OPS_ENTRYPOINTS_INDEX.md` と `ssot/OPS_ALIGNMENT_CHECKPOINTS.md` を参照。

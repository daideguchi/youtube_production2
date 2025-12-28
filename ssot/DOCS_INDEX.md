# DOCS_INDEX — SSOTドキュメント索引

この索引は `ssot/` 配下の「正本ドキュメント」をカテゴリ別に一覧化する。  
詳細は各ファイルを参照し、更新・追加時はこの索引にも反映する。

---

## 1. 最上位SSOT / 参照仕様
- `README.md`: SSOTの更新/移動/完了移設ルール（索引運用の正本）。
- `reference/REFERENCE_ssot_このプロダクト設計について.md`: 管理者の手書き設計メモ（最上位の意図・方針）。
- `ops/DATA_LAYOUT.md`: 現行データ格納の実態（SoT/生成物の場所対応）。
- `ops/OPS_IO_SCHEMAS.md`: フェーズ別I/Oスキーマ（実データ観測ベース）。
- `history/HISTORY_codex-memory.md`: 変更履歴（運用ログ）。旧履歴は `_old/ssot_old/history/` を参照。
- `history/README.md`: 履歴アーカイブ（非正本）の扱い方（旧名/旧パスが出てきたときの入口）。
- `handoffs/README.md`: 作業完走用の引き継ぎパッケージ置き場（短期・再現性重視）。
- `reference/REFERENCE_PATH_HARDCODE_INVENTORY.md`: 直書きパス/旧名参照の監査入口（現行正本）。スナップショットは `history/REFERENCE_PATH_HARDCODE_INVENTORY_20251212.md`。
- `ops/master_styles.json`: チャンネル別スタイル・画風の正本。
- `reference/【消さないで！人間用】確定ロジック.md`: 運用上の確定ルール（人間向けの最終チェック）。
- `reference/CHAT_AI_QUESTION_TEMPLATE.md`: AIへ依頼/相談するための質問テンプレ。

## 2. 運用マニュアル / OPS
- `OPS_SYSTEM_OVERVIEW.md`: このプロダクトの仕組み（全体像SSOT）。迷ったらまずこれ。
- `ops/OPS_CHANNEL_LAUNCH_MANUAL.md`: チャンネル立ち上げ・企画CSV整備・運用手順。
- `ops/OPS_CHANNEL_BENCHMARKS.md`: チャンネル別ベンチマーク（競合/台本サンプル/勝ちパターン）管理の正本。
- `packages/script_pipeline/channels/README.md` (Reference): チャンネル定義ディレクトリの補足メモ（正本: `packages/script_pipeline/channels/CHxx-*/channel_info.json` と SSOT）。
- `ops/OPS_GLOBAL_TODO.md`: 全体TODOの正本（board note thread とリンクして協働する）。
- `ops/OPS_ENTRYPOINTS_INDEX.md`: 実行入口（CLI/スクリプト/UI）の確定リスト。
- `ops/OPS_UI_WIRING.md`: UI(React) ↔ Backend(FastAPI) の配線SSOT（route/API/SoT対応）。
- `ops/OPS_SCRIPTS_PHASE_CLASSIFICATION.md`: 工程別「使う/使わない」スクリプト確定表（迷子/誤実行防止）。
- `ops/OPS_SCRIPTS_INVENTORY.md`: `scripts/**` の全ファイル棚卸し（工程別 / P0-P3 / ref付き）。
- `ops/OPS_PRODUCTION_PACK.md`: 量産投入前の「Production Pack」定義（スナップショット + QA gate + 差分ログ）。
- `ops/OPS_PREPRODUCTION_FRAME.md`: 入口〜量産投入直前の参照フレーム（SoT/拡張/差分ログの線引き）。
- `ops/OPS_PREPRODUCTION_INPUTS_CATALOG.md`: 入口〜投入前の入力カタログ（SoT/必須/任意/上書きの一覧）。
- `ops/OPS_PREPRODUCTION_REMEDIATION.md`: 入口〜投入前の“抜け漏れ”修復導線（issue→直す場所→検証）。
- `ops/OPS_REPO_DIRECTORY_SSOT.md`: リポジトリのディレクトリ構造（正本）。配置/移設/互換symlink方針の基準。
- `ops/OPS_SCRIPT_SOURCE_MAP.md`: 台本/音声/動画の“ソース元”対応表（SoT→生成物）。
- `ops/OPS_SCRIPT_FACTORY_MODES.md`: 台本工場の入口固定（new/redo-full/resume/rewrite）と運用分岐の正本。
- `ops/OPS_SCRIPT_PIPELINE_SSOT.md`: 台本量産ロジックの単一SSOT（新規/やり直し/超長尺）。
- `ops/OPS_SCRIPT_GUIDE.md`: 台本（Script）運用手順（人間の作業順）。
- `ops/OPS_SCRIPT_INCIDENT_RUNBOOK.md`: 台本がカオス化したときの止血・復帰（複数エージェント競合）のSSOT。
- `ops/OPS_A_TEXT_GLOBAL_RULES.md`: 全チャンネル共通のAテキスト執筆ルール（TTS事故を防ぐ下限品質）。
- `ops/OPS_A_TEXT_TECHNIQUE_PACKAGES.md`: Aテキストに効く“技法”をモジュール化して固定（script_prompt へ安全に差し込むためのパッケージ集）。
- `ops/OPS_SCRIPT_GENERATION_ARCHITECTURE.md`: 高品質Aテキスト大量生産の設計（パターン→生成→Judge→最小修正）。
- `ops/OPS_LONGFORM_SCRIPT_SCALING.md`: 2〜3時間級の超長尺でも破綻しない台本生成設計（Marathonモード）。
- `ops/OPS_SCRIPT_PATTERNS.yaml`: Aテキスト構成パターン集（骨格/字数配分のSSOT）。
- `ops/OPS_A_TEXT_LLM_QUALITY_GATE.md`: Aテキストの品質ゲート（LLM Judge→Fixer で「字数だけ合格」を禁止）。
- `ops/OPS_AUDIO_TTS.md`: 音声（TTS）運用手順とSoT/cleanup。
- `ops/OPS_SRT_LINEBREAK_FORMAT.md`: SRT字幕の改行整形（意味/語彙は不変、改行のみで視認性を上げる）。
- `packages/audio_tts/docs/SRT_SYNC_PROTOCOL.md` (Reference): 音声final（SRT/WAV）と video_pipeline 入力同期の契約メモ。
- `ops/OPS_PLANNING_CSV_WORKFLOW.md`: 企画/進捗CSV（Planning SoT）の運用手順。
- `ops/OPS_PLANNING_PATCHES.md`: 企画の上書き/追加/部分更新（Planning Patch）運用SSOT（差分ログ/lock前提）。
- `ops/OPS_SCRIPT_INPUT_CONTRACT.md`: Planning入力の契約（L1/L2/L3）と汚染防止ルール。
- `ops/OPS_LLM_MODEL_CHEATSHEET.md`: LLMモデル使い分け（正本: `configs/llm.yml`）。
- `ops/OPS_ENV_VARS.md`: 環境変数・キー管理の原則と必須一覧。
- `ops/OPS_CONFIRMED_PIPELINE_FLOW.md`: 現行フローの確定ロジック/確定処理フロー（フェーズ別I/O正本）。
- `ops/OPS_ARTIFACT_DRIVEN_PIPELINES.md`: THINK/API共通のartifact駆動設計（型→処理継続の原則）。
- `ops/OPS_ALIGNMENT_CHECKPOINTS.md`: SoT整合チェック（壊さないための確定チェックリスト）。
- `ops/OPS_SEMANTIC_ALIGNMENT.md`: タイトル/サムネ訴求 ↔ 台本コア の意味整合チェック/最小修正（明らかなズレのみ）。
- `ops/OPS_THUMBNAILS_PIPELINE.md`: サムネ量産/修正（ローカル合成）の運用SSOT（Compiler/retake/QC/明るさ補正）。
- `ops/OPS_LOGGING_MAP.md`: 現行ログの配置/種類/増殖経路とTarget収束先の正本マップ。
- `ops/OPS_CLEANUP_EXECUTION_LOG.md`: 実行した片付け（復元/再現可能な記録）。
- `ops/OPS_ARCHIVE_PUBLISHED_EPISODES.md`: published済みエピソードのアーカイブ（planning progress 連動、探索ノイズ削減）。
- `ops/OPS_VIDEO_RUNS_ARCHIVE_RESTORE.md`: Video runs（run_dir）の依存/参照とアーカイブ/復旧の正本。
- `ops/OPS_TTS_MANUAL_READING_AUDIT.md`: 読みLLMを使わない手動TTS監査の完全手順（全候補確認・証跡ルール）。
- `ops/OPS_CAPCUT_CH02_DRAFT_SOP.md`: CH02 CapCutドラフト生成SOP（CH02-テンプレ維持・音声挿入・字幕黒背景・機械検証）。
- `packages/video_pipeline/docs/CAPCUT_DRAFT_SOP.md` (Reference): 全チャンネル共通 CapCutドラフト生成SOP（auto_capcut_run / safe_image_swap）。
- `packages/video_pipeline/config/channel_config_spec.md` (Reference): `packages/video_pipeline/config/channel_presets.json` / `capcut_settings` の仕様メモ（実装参照）。
- `ops/IMAGE_API_PROGRESS.md`: 画像API/実装の進捗・運用メモ。
- `agent_runbooks/README.md`: agent/think-mode（Runbook/キュー運用）の入口。
- `ops/OPS_AGENT_PLAYBOOK.md`: 低知能エージェントでも迷わないための運用ルール（lock/SoT/削除/パッチ）。
- `apps/ui-backend/tools/README.md` (Reference): UI運用ツール群（start_manager / assets_sync 等）の補足。
- `apps/remotion/README.md` (Reference): Remotion（CapCut互換）の出力ワークフロー。
- `apps/remotion/REMOTION_PLAN.md` (Reference): Remotion（CapCut互換）実装計画メモ（現行実装に合わせて随時更新）。

## 3. 計画書（PLAN_*.md）

### 3.0 状態一覧
- `plans/PLAN_STATUS.md`: 計画書の状態（Active/Draft/Completed）一覧。

### 3.1 Repo / 構造
- `plans/PLAN_REPO_DIRECTORY_REFACTOR.md`: モノレポ全体のディレクトリ/生成物/レガシー再編計画。
- `completed/PLAN_STAGE1_PATH_SSOT_MIGRATION.md` (Legacy/Reference): Stage1（物理移動なし）Path SSOT導入と置換順序の正本（完了済み）。
- `plans/PLAN_LEGACY_AND_TRASH_CLASSIFICATION.md`: レガシー隔離/確実ゴミ判定の正本。

### 3.2 LLM / ルーティング
- `plans/PLAN_LLM_PIPELINE_REFACTOR.md` (Active): 台本/TTS/画像のLLM呼び出し統合計画。
- `plans/PLAN_AGENT_MODE_RUNBOOK_SYSTEM.md` (Active): API LLM を AIエージェント運用（Runbook/キュー）へ置換する計画。
- `plans/PLAN_AGENT_ORG_COORDINATION.md` (Active): Orchestrator + Workers（複数AIエージェント）協調の仕組み。
- `plans/PLAN_LLM_USAGE_MODEL_EVAL.md`: LLMコスト/トークン/モデル適性の評価計画。
- `ops/TOOLS_LLM_USAGE.md`: LLM利用の集計・可視化ツールの仕様。

#### 完了/参照（completed）
- `completed/README.md`: Completed（完了/参照）配下の索引。
- `completed/LLM_LAYER_REFACTOR_PLAN.md` (Legacy/Reference): LLMレイヤー再設計の詳細（必要に応じて `plans/PLAN_LLM_PIPELINE_REFACTOR.md` に統合）。
- `completed/LLM_ROUTING_PLAN.md` (Legacy/Reference): 旧ルーティング方針の履歴。
- `completed/PLAN_STAGE1_PATH_SSOT_MIGRATION.md` (Legacy/Reference): Path SSOT導入（Stage1）の超詳細手順（完了済み）。

### 3.3 UI / ワークスペース
- `plans/PLAN_UI_WORKSPACE_CLEANUP.md` (Active): UI整理と辞書ハブ化の計画。
- `plans/PLAN_UI_EPISODE_STUDIO.md` (Active): 企画→台本→音声→動画をUIだけで完結させる統合スタジオ計画。

### 3.4 OPS / 生成物整理
- `plans/PLAN_OPS_VOICEVOX_READING_REFORM.md` (Active): VOICEVOX読み誤り対策とTTS改善計画。
- `plans/PLAN_OPS_ARTIFACT_LIFECYCLE.md`: 中間生成物/ログ/最終成果物の保持・削除・アーカイブ規約とcleanup計画。

### 3.5 テンプレ
- `plans/PLAN_TEMPLATE.md`: 新規計画書作成テンプレ。

---

## 4. 追加/更新ルール
- 計画書は `PLAN_<DOMAIN>_<TOPIC>.md` で `ssot/plans/` に追加する。**完了/Closed になった計画書は `ssot/completed/` に移動**し、`ssot/plans/` は現行作業の索引に保つ。
- Legacy と明記されたものは参考用。新規実装の根拠は必ず Active な PLAN を参照する。
- 追加・変更したら `ssot/README.md` と本索引にリンクを追記する。

# SSOT (Single Source of Truth)

- このディレクトリは最新の設計・運用ドキュメントの正本です。旧階層は `ssot_old/` に移動しました。
- ドキュメント全体の索引は `DOCS_INDEX.md` を参照してください。
- VOICEVOX 読み誤り対策の計画書（設計・実装・TODO 管理）は `PLAN_OPS_VOICEVOX_READING_REFORM.md` を参照してください。
- LLM パイプライン統合計画は `PLAN_LLM_PIPELINE_REFACTOR.md` を参照してください。
- 新規の計画書を作成する場合は、本 README の命名規則に従い、`PLAN_TEMPLATE.md` をコピーして着手してください。
- 新しいドキュメントを追加する場合は本ディレクトリに配置し、必要に応じて本 README へリンクを追記してください。

## 計画書の命名規則と作成手順

- **命名規則**: `PLAN_<ドメイン>_<テーマ>.md`（全て大文字のスネークケース）。例: `PLAN_LLM_PIPELINE_REFACTOR.md`, `PLAN_OPS_VOICEVOX_READING_REFORM.md`。
- **配置場所**: Active/Draft の計画書は SSOT 直下に置く。**完了/Closed の計画書は `ssot/completed/` に移動して保管**し、直下を現行作業の一覧に保つ。
- **テンプレ使用**: 新規作成時は `PLAN_TEMPLATE.md` をコピーし、メタデータとセクションを必ず埋める。
- **参照リンク**: 追加した計画書は README に追記し、用途と範囲が分かる 1 行説明を付ける。

## 計画書一覧
### Repo / 構造
- `PLAN_REPO_DIRECTORY_REFACTOR.md`: モノレポ全体のディレクトリ/生成物/レガシー再編計画。
- `PLAN_STAGE1_PATH_SSOT_MIGRATION.md`: Stage1（物理移動なし）でのPath SSOT導入・直書きパス置換の超詳細手順。
- `PLAN_LEGACY_AND_TRASH_CLASSIFICATION.md`: レガシー隔離/確実ゴミ判定の基準と段階実行計画。

### LLM / ルーティング
- `PLAN_LLM_PIPELINE_REFACTOR.md` (Active): 台本/TTS/画像のLLM呼び出し統合計画。
- `PLAN_LLM_USAGE_MODEL_EVAL.md`: LLMコスト/トークン/モデル適性の評価計画。
- `TOOLS_LLM_USAGE.md`: LLM利用の集計・可視化ツールの仕様。

#### 完了/参照（completed）
- `completed/LLM_LAYER_REFACTOR_PLAN.md` (Legacy/Reference): LLMレイヤー再設計の詳細（必要に応じて統合）。
- `completed/LLM_ROUTING_PLAN.md` (Legacy/Reference): 旧ルーティング方針の履歴。

### UI / ワークスペース
- `PLAN_UI_WORKSPACE_CLEANUP.md` (Active): UI整理と辞書ハブ化の計画。

### OPS / 生成物整理
- `PLAN_OPS_VOICEVOX_READING_REFORM.md` (Active): VOICEVOX読み誤り対策とTTS改善計画。
- `PLAN_OPS_ARTIFACT_LIFECYCLE.md`: 中間生成物/ログ/最終成果物の保持・削除・アーカイブ規約とcleanup計画。

## 運用マニュアル
- `OPS_CHANNEL_LAUNCH_MANUAL.md`: テーマ入力後に AI エージェントが 30 本の企画 CSV とペルソナを整備し、「企画準備完了」に到達するための手順書。
- `OPS_CONFIRMED_PIPELINE_FLOW.md`: 現行の確定処理ロジック/処理フローとフェーズ別I/Oの正本。
- `OPS_LOGGING_MAP.md`: 現行のログ配置/種類/増殖経路と、Target収束先の正本マップ。
- `OPS_TTS_MANUAL_READING_AUDIT.md`: 読みLLMを使わない手動TTS監査フロー（全候補確認・辞書/位置パッチ・証跡記録）。

## 参照仕様
- `REFERENCE_ssot_このプロダクト設計について`: 最上位の設計意図。
- `DATA_LAYOUT.md`: 現行データ格納の実態。
- `REFERENCE_PATH_HARDCODE_INVENTORY.md`: 直書きパス/旧名参照の完全棚卸し（Stage1置換の正本）。
- `master_styles.json`: チャンネル別スタイル正本。
- `IMAGE_API_PROGRESS.md`: 画像API/実装の進捗・運用メモ。
- `【消さないで！人間用】確定ロジック`: 運用上の確定ルール。

## 環境変数の原則
- 秘密鍵（例: `GEMINI_API_KEY`）はリポジトリ直下の `.env` もしくはシェル環境変数に一元管理する。`.gemini_config` や `credentials/` 配下への複製は禁止。
- 具体的な必須キー一覧やポートは `ssot/OPS_ENV_VARS.md` を参照。

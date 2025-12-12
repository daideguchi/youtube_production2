# DOCS_INDEX — SSOTドキュメント索引

この索引は `ssot/` 配下の「正本ドキュメント」をカテゴリ別に一覧化する。  
詳細は各ファイルを参照し、更新・追加時はこの索引にも反映する。

---

## 1. 最上位SSOT / 参照仕様
- `REFERENCE_ssot_このプロダクト設計について`: 管理者の手書き設計メモ（最上位の意図・方針）。
- `DATA_LAYOUT.md`: 現行データ格納の実態（SoT/生成物の場所対応）。
- `REFERENCE_PATH_HARDCODE_INVENTORY.md`: 直書きパス/旧名参照の完全棚卸し（Path SSOT導入の前提）。
- `master_styles.json`: チャンネル別スタイル・画風の正本。
- `【消さないで！人間用】確定ロジック`: 運用上の確定ルール（人間向けの最終チェック）。

## 2. 運用マニュアル / OPS
- `OPS_CHANNEL_LAUNCH_MANUAL.md`: チャンネル立ち上げ・企画CSV整備・運用手順。
- `OPS_ENV_VARS.md`: 環境変数・キー管理の原則と必須一覧。
- `OPS_CONFIRMED_PIPELINE_FLOW.md`: 現行フローの確定ロジック/確定処理フロー（フェーズ別I/O正本）。
- `OPS_LOGGING_MAP.md`: 現行ログの配置/種類/増殖経路とTarget収束先の正本マップ。
- `OPS_TTS_MANUAL_READING_AUDIT.md`: 読みLLMを使わない手動TTS監査の完全手順（全候補確認・証跡ルール）。
- `IMAGE_API_PROGRESS.md`: 画像API/実装の進捗・運用メモ。

## 3. 計画書（PLAN_*.md）

### 3.1 Repo / 構造
- `PLAN_REPO_DIRECTORY_REFACTOR.md`: モノレポ全体のディレクトリ/生成物/レガシー再編計画。
- `PLAN_STAGE1_PATH_SSOT_MIGRATION.md`: Stage1（物理移動なし）Path SSOT導入と置換順序の正本。
- `PLAN_LEGACY_AND_TRASH_CLASSIFICATION.md`: レガシー隔離/確実ゴミ判定の正本。

### 3.2 LLM / ルーティング
- `PLAN_LLM_PIPELINE_REFACTOR.md` (Active): 台本/TTS/画像のLLM呼び出し統合計画。
- `PLAN_LLM_USAGE_MODEL_EVAL.md`: LLMコスト/トークン/モデル適性の評価計画。
- `TOOLS_LLM_USAGE.md`: LLM利用の集計・可視化ツールの仕様。

#### 完了/参照（completed）
- `completed/LLM_LAYER_REFACTOR_PLAN.md` (Legacy/Reference): LLMレイヤー再設計の詳細（必要に応じて `PLAN_LLM_PIPELINE_REFACTOR.md` に統合）。
- `completed/LLM_ROUTING_PLAN.md` (Legacy/Reference): 旧ルーティング方針の履歴。

### 3.3 UI / ワークスペース
- `PLAN_UI_WORKSPACE_CLEANUP.md` (Active): UI整理と辞書ハブ化の計画。

### 3.4 OPS / 生成物整理
- `PLAN_OPS_VOICEVOX_READING_REFORM.md` (Active): VOICEVOX読み誤り対策とTTS改善計画。
- `PLAN_OPS_ARTIFACT_LIFECYCLE.md`: 中間生成物/ログ/最終成果物の保持・削除・アーカイブ規約とcleanup計画。

### 3.5 テンプレ
- `PLAN_TEMPLATE.md`: 新規計画書作成テンプレ。

---

## 4. 追加/更新ルール
- 計画書は `PLAN_<DOMAIN>_<TOPIC>.md` で SSOT 直下に追加する。**完了/Closed になった計画書は `ssot/completed/` に移動**し、直下は現行作業の索引に保つ。
- Legacy と明記されたものは参考用。新規実装の根拠は必ず Active な PLAN を参照する。
- 追加・変更したら `ssot/README.md` と本索引にリンクを追記する。

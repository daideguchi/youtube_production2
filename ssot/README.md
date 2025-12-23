# SSOT (Single Source of Truth)

- このディレクトリは最新の設計・運用ドキュメントの正本です。
- **索引の正本**: `DOCS_INDEX.md`
- **全体TODO（正本）**: `ops/OPS_GLOBAL_TODO.md`（日々のやり取りは board thread へ）
- **計画書の状態一覧**: `plans/PLAN_STATUS.md`
- **入口（迷わない）**: `../START_HERE.md`
- **変更履歴（運用ログ）の正本**: `history/HISTORY_codex-memory.md`
- 引き継ぎパッケージ: `handoffs/README.md`
- 完了した計画書: `completed/README.md`

## 計画書の命名規則と作成手順

- **命名規則**: `PLAN_<ドメイン>_<テーマ>.md`（全て大文字のスネークケース）。例: `PLAN_LLM_PIPELINE_REFACTOR.md`, `PLAN_OPS_VOICEVOX_READING_REFORM.md`。
- **配置場所**: Active/Draft の計画書は `ssot/plans/` に置く。**完了/Closed の計画書は `ssot/completed/` に移動して保管**し、`ssot/plans/` を現行作業の一覧に保つ。
- **テンプレ使用**: 新規作成時は `plans/PLAN_TEMPLATE.md` をコピーし、メタデータとセクションを必ず埋める。
- **参照リンク**: 追加/更新したら `DOCS_INDEX.md` に必ず追記する（READMEの一覧は持たない＝二重管理を避ける）。

## 更新ルール（迷わないための固定）

- 新規ドキュメントを追加/改名/移動したら `DOCS_INDEX.md` を必ず更新する（READMEに一覧は持たない）。
- Closed（完了）になった計画書は `ssot/completed/` に移動する。
- 索引整合は `python3 scripts/ops/ssot_audit.py` で確認する。

## 環境変数の原則
- 秘密鍵（例: `GEMINI_API_KEY`）はリポジトリ直下の `.env` もしくはシェル環境変数に一元管理する。`.gemini_config` や `credentials/` 配下への複製は禁止。
- 具体的な必須キー一覧は `ssot/ops/OPS_ENV_VARS.md` を参照。導線/整合チェックは `ssot/ops/OPS_ENTRYPOINTS_INDEX.md` と `ssot/ops/OPS_ALIGNMENT_CHECKPOINTS.md` を参照。

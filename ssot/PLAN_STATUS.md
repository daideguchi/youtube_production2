# PLAN_STATUS — 計画書の状態一覧（SSOT）

SSOT 配下の計画書（`PLAN_*.md`）が増えてきたため、**状態（Draft/Active/Paused/Closed）別**に一覧化する。

- Active/Draft/Paused の計画書は `ssot/` 直下に置く
- **Closed（完了）になった計画書は `ssot/completed/` に移動**する（直下を「現行作業の一覧」に保つ）

更新ルールは `ssot/README.md` と `ssot/DOCS_INDEX.md` を正とする。

---

## Active（進行中）
- `PLAN_UI_EPISODE_STUDIO.md`（最終更新 2025-12-13）: 企画→台本→音声→動画を UI だけで完結させる統合スタジオ。
- `PLAN_UI_WORKSPACE_CLEANUP.md`（最終更新 2025-12-12）: UI の導線整理と辞書/ハブ化。
- `PLAN_OPS_VOICEVOX_READING_REFORM.md`（最終更新 2025-12-11）: VOICEVOX 読み誤り対策（実装/運用 TODO 管理の正本）。
- `PLAN_LLM_PIPELINE_REFACTOR.md`（最終更新 2025-12-10）: 台本/TTS/画像の LLM 呼び出し統合。
- `PLAN_AGENT_MODE_RUNBOOK_SYSTEM.md`（最終更新 2025-12-12）: API LLM を agent/think-mode（Runbook/キュー）へ置換。
- `PLAN_AGENT_ORG_COORDINATION.md`（最終更新 2025-12-12）: Orchestrator + Workers 協調（スコープロック/申し送り/割当）。

## Draft（設計中）
- `PLAN_REPO_DIRECTORY_REFACTOR.md`（最終更新 2025-12-12）: モノレポ全体のディレクトリ再編（段階移行）。
- `PLAN_OPS_ARTIFACT_LIFECYCLE.md`（最終更新 2025-12-12）: 生成物/ログ/中間物の保持・削除・アーカイブ規約（cleanup）。
- `PLAN_LEGACY_AND_TRASH_CLASSIFICATION.md`（最終更新 2025-12-12）: レガシー隔離/確実ゴミ判定の基準と段階実行。
- `PLAN_LLM_USAGE_MODEL_EVAL.md`（最終更新 2025-12-12）: LLM コスト/トークン/モデル適性の評価。

## Completed（完了/参照）
`ssot/completed/` を参照。

- `completed/LLM_LAYER_REFACTOR_PLAN.md`
- `completed/LLM_ROUTING_PLAN.md`
- `completed/PLAN_STAGE1_PATH_SSOT_MIGRATION.md`

## Template
- `PLAN_TEMPLATE.md`: 新規計画書のテンプレ。

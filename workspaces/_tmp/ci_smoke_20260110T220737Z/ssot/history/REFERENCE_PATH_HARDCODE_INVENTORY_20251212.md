# REFERENCE_PATH_HARDCODE_INVENTORY — Stage1 直書きパス棚卸し（完全リスト）

> 重要（現状）:
> - 本書は **2025-12-12 時点のスナップショット**です（Stage1: Path SSOT 導入の材料）。
> - 現行コードは既に修正が進んでいるため、**このリストがそのまま現状を表すとは限りません**（SSOT汚染/誤誘導防止）。
>
> 現行の「直書き/旧名混入」検知は、以下を正とする:
> - `python3 scripts/ops/repo_sanity_audit.py --verbose`（root互換symlink再混入/探索ノイズをガード）
> - `python3 scripts/ops/repo_ref_audit.py --target <path-or-glob> --stdout`（参照ゼロ/参照棚卸し）
> - 直書きgrep（例）: `rg --files-with-matches "/Users/dd/" apps packages scripts tests`

検出日: 2025-12-12  
目的: `packages/factory_common/paths.py`（Path SSOT）導入前に、直書きパス残存箇所を**全量把握**し、Stage 1 の置換順序とリスク評価の正本にする。

> 方針: **Stage 1 では “実行コード層のみ置換”**（apps/packages/scripts/tools）。  
> `ssot/`, `docs/`, `workspaces/planning/`, `workspaces/scripts/` や `packages/commentary_02_srt2images_timeline/tools/archive/*` のような参照/履歴/生成物は、物理移動後のドキュメント同期か Stage 3 で整理する。

---

## A. 絶対パス `/Users/dd/` 残存

`rg --files-with-matches "/Users/dd/"` の結果。  
大量にヒットするため **実行コードで重要なもののみ抜粋**し、その他は「docs/legacy/生成物」として後回し。

**Active/実行コード（Stage 1 で置換必須）**
- `packages/commentary_02_srt2images_timeline/tools/safe_image_swap.py`
- `packages/commentary_02_srt2images_timeline/tools/comprehensive_validation.py`
- `packages/commentary_02_srt2images_timeline/tools/capcut_title_updater.py`
- `packages/commentary_02_srt2images_timeline/tools/analysis/capcut_draft_analyzer.py`
- `packages/commentary_02_srt2images_timeline/tools/analysis/draft_analysis_struct.py`
- `packages/commentary_02_srt2images_timeline/tools/maintenance/fix_capcut_draft_image_binding.py`
- `packages/commentary_02_srt2images_timeline/tools/maintenance/relink_capcut_photo_materials.py`
- `packages/commentary_02_srt2images_timeline/tools/maintenance/audit_capcut_photo_refs.py`
- `packages/commentary_02_srt2images_timeline/tools/sync_material_names_and_ids_safe.py`
- `packages/commentary_02_srt2images_timeline/tools/sync_srt2images_materials.py`
- `packages/commentary_02_srt2images_timeline/src/capcut_ui/core/draft_manager.py`
- `packages/commentary_02_srt2images_timeline/src/config/llm_resolver.py`
- `packages/commentary_02_srt2images_timeline/src/ui/capcut_template_manager.py`
- `scripts/create_image_cues_from_srt.py`
- `scripts/fix_ch02_row.py`
- `scripts/check_ch02_content.py`
- `scripts/check_ch02_quality.py`
- `scripts/repair_manager.py`
- `scripts/scaffold_project.py`
- `scripts/append_ch02_row.py`
- `scripts/drive_oauth_setup.py`
- `scripts/youtube_publisher/oauth_setup.py`
- `packages/script_pipeline/runner.py`
- `apps/remotion/scripts/gen_belt_from_srt.js`（Remotionはexperimental）
- `tests/test_model_selection.py`（現行対象テスト）

**Legacy/Docs/生成物（Stage 1 では触らない）**
- `ssot/*`, `README.md`, `configs/README.md`, `apps/remotion/REMOTION_PLAN.md`
- `workspaces/planning/channels/*.csv`, `workspaces/planning/templates/*.csv`
- `workspaces/scripts/**`（生成物/SoT）
- `packages/commentary_02_srt2images_timeline/tools/archive/**`（過去版バックアップ）
- `50_tools/**`, `_old/**`, `docs/**`

---

## B. `script_pipeline/data` 直書き

`rg --files-with-matches "script_pipeline/data"` の結果（全件）。

- `README.md`
- `packages/audio_tts_v2/scripts/run_tts.py`
- `ssot/agent_runbooks/RUNBOOK_JOB_RUNNER_DAEMON.md`（Docs）
- `ssot/completed/PLAN_STAGE1_PATH_SSOT_MIGRATION.md`（Docs）
- `ssot/ops/DATA_LAYOUT.md`（Docs）
- `ssot/ops/OPS_AGENT_PLAYBOOK.md`（Docs）
- `ssot/ops/OPS_ALIGNMENT_CHECKPOINTS.md`（Docs）
- `ssot/ops/OPS_ARTIFACT_DRIVEN_PIPELINES.md`（Docs）
- `ssot/ops/OPS_AUDIO_TTS.md`（Docs）
- `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`（Docs）
- `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`（Docs）
- `ssot/ops/OPS_IO_SCHEMAS.md`（Docs）
- `ssot/ops/OPS_LOGGING_MAP.md`（Docs）
- `ssot/ops/OPS_PLANNING_CSV_WORKFLOW.md`（Docs）
- `ssot/ops/OPS_SCRIPT_GUIDE.md`（Docs）
- `ssot/ops/OPS_SCRIPT_SOURCE_MAP.md`（Docs）
- `ssot/ops/OPS_TTS_MANUAL_READING_AUDIT.md`（Docs）
- `ssot/plans/PLAN_LLM_PIPELINE_REFACTOR.md`（Docs）
- `ssot/plans/PLAN_OPS_ARTIFACT_LIFECYCLE.md`（Docs）
- `ssot/plans/PLAN_REPO_DIRECTORY_REFACTOR.md`（Docs）
- `ssot/plans/PLAN_UI_EPISODE_STUDIO.md`（Docs）
- `ssot/reference/REFERENCE_PATH_HARDCODE_INVENTORY.md`（Docs）
- `ssot/reference/REFERENCE_ssot_このプロダクト設計について.md`（Docs）
- `ssot/reference/【消さないで！人間用】確定ロジック.md`（Docs）
- `workspaces/README.md`（Docs）
- `workspaces/planning/README.md`（Docs）
- `workspaces/planning/channels/CH03.csv`（SoT/Planning）

置換先: `factory_common.paths.script_data_root()` / `video_root(ch, vid)` / `status_path(ch, vid)` を使用。

---

## C. `commentary_02_srt2images_timeline/output` 直書き

`rg --files-with-matches "commentary_02_srt2images_timeline/output"` の結果（全件）。

- `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`
- `ssot/ops/OPS_IO_SCHEMAS.md`
- `ssot/ops/OPS_ALIGNMENT_CHECKPOINTS.md`
- `ssot/plans/PLAN_UI_EPISODE_STUDIO.md`
- `ssot/ops/DATA_LAYOUT.md`
- `ssot/reference/REFERENCE_PATH_HARDCODE_INVENTORY.md`
- `ssot/handoffs/CH02_IMAGES_NOISE_FIX/HANDOFF.md`
- `ssot/completed/PLAN_STAGE1_PATH_SSOT_MIGRATION.md`
- `ssot/reference/REFERENCE_ssot_このプロダクト設計について.md`
- `ssot/plans/PLAN_REPO_DIRECTORY_REFACTOR.md`
- `workspaces/README.md`
- `ssot/ops/OPS_CAPCUT_CH02_DRAFT_SOP.md`
- `ssot/ops/OPS_AGENT_PLAYBOOK.md`
- `ssot/ops/OPS_ARTIFACT_DRIVEN_PIPELINES.md`
- `ssot/plans/PLAN_OPS_ARTIFACT_LIFECYCLE.md`
- `ssot/ops/OPS_LOGGING_MAP.md`
- `ssot/ops/OPS_SCRIPT_SOURCE_MAP.md`
- `ssot/reference/【消さないで！人間用】確定ロジック.md`

置換先: `factory_common.paths.video_runs_root()` / `video_run_dir(run_id)`。

---

## D. `audio_tts_v2/artifacts` 直書き

`rg --files-with-matches "audio_tts_v2/artifacts"` の結果（全件）。

- `ssot/ops/OPS_ENV_VARS.md`
- `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`
- `ssot/ops/OPS_IO_SCHEMAS.md`
- `ssot/ops/OPS_ENTRYPOINTS_INDEX.md`
- `ssot/plans/PLAN_UI_EPISODE_STUDIO.md`
- `ssot/handoffs/CH02_IMAGES_NOISE_FIX/HANDOFF.md`
- `ssot/ops/OPS_ALIGNMENT_CHECKPOINTS.md`
- `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`
- `ssot/ops/DATA_LAYOUT.md`
- `ssot/reference/REFERENCE_PATH_HARDCODE_INVENTORY.md`

置換先: `factory_common.paths.audio_root()` / `audio_final_dir(ch, vid)`。

---

## E. `progress/channels` 直書き

`rg --files-with-matches "progress/channels"` の結果（全件）。

- `apps/ui-backend/backend/main.py`
- `apps/ui-frontend/src/api/client.ts`
- `apps/ui-frontend/src/layouts/AppShell.tsx`
- `apps/ui-frontend/src/pages/ProjectsPage.tsx`
- `apps/ui-frontend/src/pages/ScriptFactoryPage.tsx`
- `README.md`
- `ssot/completed/PLAN_STAGE1_PATH_SSOT_MIGRATION.md`
- `ssot/ops/DATA_LAYOUT.md`
- `ssot/ops/OPS_AGENT_PLAYBOOK.md`
- `ssot/ops/OPS_ALIGNMENT_CHECKPOINTS.md`
- `ssot/ops/OPS_CHANNEL_LAUNCH_MANUAL.md`
- `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`
- `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`
- `ssot/ops/OPS_ENTRYPOINTS_INDEX.md`
- `ssot/ops/OPS_IO_SCHEMAS.md`
- `ssot/ops/OPS_PLANNING_CSV_WORKFLOW.md`
- `ssot/ops/OPS_SCRIPT_GUIDE.md`
- `ssot/ops/OPS_SCRIPT_SOURCE_MAP.md`
- `ssot/plans/PLAN_OPS_ARTIFACT_LIFECYCLE.md`
- `ssot/plans/PLAN_REPO_DIRECTORY_REFACTOR.md`
- `ssot/plans/PLAN_TEMPLATE.md`
- `ssot/plans/PLAN_UI_EPISODE_STUDIO.md`
- `ssot/plans/PLAN_UI_WORKSPACE_CLEANUP.md`
- `ssot/reference/REFERENCE_PATH_HARDCODE_INVENTORY.md`
- `ssot/reference/REFERENCE_ssot_このプロダクト設計について.md`
- `ssot/reference/【消さないで！人間用】確定ロジック.md`
- `workspaces/planning/personas/CH01_PERSONA.md`
- `workspaces/planning/personas/CH02_PERSONA.md`
- `workspaces/planning/personas/CH03_PERSONA.md`
- `workspaces/planning/personas/CH04_PERSONA.md`
- `workspaces/planning/personas/CH05_PERSONA.md`
- `workspaces/planning/personas/CH06_PERSONA.md`
- `workspaces/planning/personas/CH09_PERSONA.md`
- `workspaces/planning/personas/CH10_PERSONA.md`
- `workspaces/planning/personas/CH11_PERSONA.md`

置換先: `factory_common.paths.planning_root()` / `channels_csv_path(ch)`。

---

## F. `thumbnails/assets` 直書き

`rg --files-with-matches "thumbnails/assets"` の結果（全件）。

- `apps/ui-backend/backend/main.py`
- `apps/ui-frontend/src/components/ThumbnailWorkspace.tsx`
- `apps/ui-backend/tools/assets_sync.py`
- `apps/ui-backend/tools/README.md`（Docs）
- `workspaces/thumbnails/README.md`（Docs）
- `workspaces/thumbnails/ui/thumbnail_workspace_plan.md`（Docs）
- `ssot/*`（Docs）

置換先: `factory_common.paths.thumbnails_root()` / `thumbnail_assets_dir(ch, vid)`。

---

## G. 旧名 `commentary_01_srtfile_v2` 参照

`rg --files-with-matches "commentary_01_srtfile_v2"` の結果（全件）。  
現行ではディレクトリ実体が無く、**全てLegacy参照**。

- `apps/ui-backend/backend/main.py`（コメント/互換メモ）
- `apps/ui-backend/backend/video_production.py`（コメント/互換メモ）
- `scripts/validate_status_sweep.py` / `scripts/force_asset_sync.py`（legacy置換の説明文）
- `ssot/*`（計画/cleanupログ/履歴）
- `workspaces/planning/personas/*.md`（運用メモ）

方針:
- Stage 1 では「paths SSOT 化 or legacy へ隔離」のため参照を**薄く置換**。
- コード/テストからは削除済み。残存する参照は Docs/履歴として扱い、再導入しない。

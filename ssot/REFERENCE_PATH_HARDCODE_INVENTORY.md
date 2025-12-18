# REFERENCE_PATH_HARDCODE_INVENTORY — Stage1 直書きパス棚卸し（完全リスト）

検出日: 2025-12-12  
目的: `factory_common/paths.py`（Path SSOT）導入前に、直書きパス残存箇所を**全量把握**し、Stage 1 の置換順序とリスク評価の正本にする。

> 方針: **Stage 1 では “実行コード層のみ置換”**（apps/packages/scripts/tools）。  
> `ssot/`, `docs/`, `progress/`, `script_pipeline/data/` や `commentary_02_srt2images_timeline/tools/archive/*` のような参照/履歴/生成物は、物理移動後のドキュメント同期か Stage 3 で整理する。

---

## A. 絶対パス `/Users/dd/` 残存

`rg --files-with-matches "/Users/dd/"` の結果。  
大量にヒットするため **実行コードで重要なもののみ抜粋**し、その他は「docs/legacy/生成物」として後回し。

**Active/実行コード（Stage 1 で置換必須）**
- `commentary_02_srt2images_timeline/tools/safe_image_swap.py`
- `commentary_02_srt2images_timeline/tools/comprehensive_validation.py`
- `commentary_02_srt2images_timeline/tools/capcut_title_updater.py`
- `commentary_02_srt2images_timeline/tools/analysis/capcut_draft_analyzer.py`
- `commentary_02_srt2images_timeline/tools/analysis/draft_analysis_struct.py`
- `commentary_02_srt2images_timeline/tools/maintenance/fix_capcut_draft_image_binding.py`
- `commentary_02_srt2images_timeline/tools/maintenance/relink_capcut_photo_materials.py`
- `commentary_02_srt2images_timeline/tools/maintenance/audit_capcut_photo_refs.py`
- `commentary_02_srt2images_timeline/tools/sync_material_names_and_ids_safe.py`
- `commentary_02_srt2images_timeline/tools/sync_srt2images_materials.py`
- `commentary_02_srt2images_timeline/src/capcut_ui/core/draft_manager.py`
- `commentary_02_srt2images_timeline/src/config/llm_resolver.py`
- `commentary_02_srt2images_timeline/src/ui/capcut_template_manager.py`
- `commentary_02_srt2images_timeline/ui/gradio_app.py`
- `scripts/create_image_cues_from_srt.py`
- `scripts/fix_ch02_row.py`
- `scripts/check_ch02_content.py`
- `scripts/check_ch02_quality.py`
- `scripts/repair_manager.py`
- `scripts/scaffold_project.py`
- `scripts/append_ch02_row.py`
- `scripts/drive_oauth_setup.py`
- `scripts/youtube_publisher/oauth_setup.py`
- `script_pipeline/runner.py`
- `remotion/scripts/gen_belt_from_srt.js`（Remotionはexperimental）
- `tests/test_model_selection.py`（現行対象テスト）

**Legacy/Docs/生成物（Stage 1 では触らない）**
- `ssot/*`, `README.md`, `configs/README.md`, `remotion/REMOTION_PLAN.md`
- `progress/channels/*.csv`, `progress/templates/*.csv`
- `script_pipeline/data/**`（生成物/SoT）
- `commentary_02_srt2images_timeline/tools/archive/**`（過去版バックアップ）
- `50_tools/**`, `_old/**`, `docs/**`

---

## B. `script_pipeline/data` 直書き

`rg --files-with-matches "script_pipeline/data"` の結果（全件）。

- `apps/ui-backend/backend/main.py`（互換: `ui/backend/*` は symlink）
- `apps/ui-backend/backend/routers/tts_progress.py`
- `apps/ui-frontend/src/api/client.ts`（互換: `ui/frontend/src/...` は symlink）
- `apps/ui-frontend/src/components/ResearchWorkspace.tsx`
- `audio_tts_v2/scripts/run_tts.py`
- `audio_tts_v2/legacy_archive/scripts/prepare_inputs.py`（Legacy）
- `script_pipeline/sot.py`
- `script_pipeline/job_runner.py`
- `scripts/check_ch02_quality.py`
- `scripts/check_ch02_content.py`
- `scripts/regenerate_audio.py`
- `scripts/regenerate_strict.py`
- `scripts/run_ch03_batch.sh`
- `scripts/regenerate_ch05_audio_no_llm.sh`
- `scripts/verify_srt_sync.py`
- `scripts/apply_reading_corrections.py`
- `scripts/sync_all_scripts.py`
- `scripts/job_runner_service.md`（Docs）
- `ssot/history/HISTORY_tts_reading_audit.md`（Docs）
- `docs/static/js/main.281eca7e.js` / `.map`（ビルド生成物）
- `progress/channels/*.csv`, `progress/templates/*.csv`（SoT/テンプレ）
- `ssot/*`（Docs）

置換先: `factory_common.paths.script_data_root()` / `video_root(ch, vid)` / `status_path(ch, vid)` を使用。

---

## C. `commentary_02_srt2images_timeline/output` 直書き

`rg --files-with-matches "commentary_02_srt2images_timeline/output"` の結果（全件）。

- `scripts/create_image_cues_from_srt.py`
- `scripts/run_pipeline_skip_llm.py`
- `apps/ui-backend/backend/routers/swap.py`
- `commentary_02_srt2images_timeline/system_prompt_for_image_generation.txt`（Docs/運用）
- `apps/ui-frontend/src/components/RemotionWorkspace.tsx`（experimental UI）
- `ssot/*`（Docs）

置換先: `factory_common.paths.video_runs_root()` / `video_run_dir(run_id)`。

---

## D. `audio_tts_v2/artifacts` 直書き

`rg --files-with-matches "audio_tts_v2/artifacts"` の結果（全件）。

- `audio_tts_v2/scripts/run_tts.py`
- `commentary_02_srt2images_timeline/tools/sync_audio_inputs.py`
- `apps/ui-backend/backend/routers/auto_draft.py`
- `apps/ui-frontend/src/pages/AutoDraftPage.tsx`
- `apps/ui-frontend/src/components/AudioWorkspace.tsx`
- `scripts/check_all_srt.sh`
- `scripts/mass_regenerate_strict.sh`
- `scripts/sequential_repair.sh`
- `scripts/audit_all.sh`
- `scripts/regenerate_ch05_audio_no_llm.sh`
- `audio_tts_v2/legacy_archive/scripts/*`（Legacy）
- `audio_tts_v2/docs/SRT_SYNC_PROTOCOL.md`（Docs）
- `script_pipeline/stages.yaml`（Docs/定義）
- `ssot/*`（Docs）

置換先: `factory_common.paths.audio_root()` / `audio_final_dir(ch, vid)`。

---

## E. `progress/channels` 直書き

`rg --files-with-matches "progress/channels"` の結果（全件）。

- `apps/ui-backend/backend/main.py`
- `apps/ui-frontend/src/pages/ScriptFactoryPage.tsx`
- `apps/ui-frontend/src/pages/ProjectsPage.tsx`
- `apps/ui-frontend/src/api/client.ts`
- `apps/ui-frontend/src/layouts/AppShell.tsx`
- `tools/check_consistency.py`
- `tools/check_ch06_quality.py`
- `tools/audit_and_enhance_ch06.py`
- `tools/final_audit_ch06.py`
- `tools/enhance_thumbnail_prompts.py`
- `tools/clean_thumbnail_prompts.py`
- `scripts/fix_ch02_row.py`
- `scripts/append_ch02_row.py`
- `apps/ui-backend/tools/assets_sync.py`（互換: `ui/tools/assets_sync.py`）
- `configs/sources.yaml`
- `script_pipeline/config/sources.yaml`
- `commentary_02_srt2images_timeline/tools/capcut_bulk_insert.py`
- `progress/personas/*.md` / `progress/README.md`（SoT/Docs）
- `ssot/*`（Docs）

置換先: `factory_common.paths.planning_root()` / `channels_csv_path(ch)`。

---

## F. `thumbnails/assets` 直書き

`rg --files-with-matches "thumbnails/assets"` の結果（全件）。

- `apps/ui-backend/backend/main.py`
- `apps/ui-frontend/src/components/ThumbnailWorkspace.tsx`
- `apps/ui-backend/tools/assets_sync.py`（互換: `ui/tools/assets_sync.py`）
- `ui/tools/README.md`（Docs）
- `thumbnails/README.md`（Docs）
- `thumbnails/ui/thumbnail_workspace_plan.md`（Docs）
- `ssot/*`（Docs）

置換先: `factory_common.paths.thumbnails_root()` / `thumbnail_assets_dir(ch, vid)`。

---

## G. 旧名 `commentary_01_srtfile_v2` 参照

`rg --files-with-matches "commentary_01_srtfile_v2"` の結果（全件）。  
現行ではディレクトリ実体が無く、**全てLegacy参照**。

- `apps/ui-backend/backend/main.py`（コメント/互換メモ）
- `apps/ui-backend/backend/video_production.py`（コメント/互換メモ）
- `scripts/validate_status_sweep.py` / `scripts/force_asset_sync.py`（legacy置換の説明文）
- `packages/audio_tts_v2/README.md`（互換メモ）
- `ssot/*`（計画/cleanupログ/履歴）
- `workspaces/planning/personas/*.md`（運用メモ）

方針:
- Stage 1 では「paths SSOT 化 or legacy へ隔離」のため参照を**薄く置換**。
- コード/テストからは削除済み。残存する参照は Docs/履歴として扱い、再導入しない。

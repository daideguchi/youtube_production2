# OPS_SCRIPTS_INVENTORY — scripts/ 全ファイル棚卸し（工程別 / 使う・使わない）

生成:
- `python3 scripts/ops/scripts_inventory.py --write`

目的:
- `scripts/**` を **全量**列挙し、工程（Phase）と分類（P0/P1/P2/P3）を 1 行ずつ確定する。
- ゴミ判定ミス（例: `run_srt2images.sh` のような間接呼び出し）を防ぐため、ref（参照元）も併記する。

正本:
- フロー: `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`
- 入口/方針: `ssot/ops/OPS_SCRIPTS_PHASE_CLASSIFICATION.md`

凡例:
- `P0`: 正規入口（主線・まず叩く）
- `P1`: 付帯/診断（使うことはあるが主線ではない）
- `P2`: 禁止（絶対に使わない / 削除候補）
- `P3`: 一時（`scripts/_adhoc/`。原則git管理しない）

ref の見方:
- `apps=*` / `packages=*` / `ui=*` は **コード参照**（自動実行の可能性が高い）
- `scripts=*` は **スクリプト間依存**（他の運用スクリプトが呼ぶ可能性）
- `ssot=*` / `README=*` は **ドキュメント参照**（手動実行の可能性）
- `refs=0` かつ SSOT未記載のものは “未確認レガシー候補” として扱い、削除は `PLAN_LEGACY_AND_TRASH_CLASSIFICATION` の条件を満たしてから行う。

---

| script | phase | P | listed-in-SSOT | refs (apps/packages/ui/scripts/ssot/readme/other) | example ref |
|---|---:|:--:|:--:|---:|---|
| `scripts/_adhoc/README.md` | MISC | P3 | yes | refs=0 | `-` |
| `scripts/_bootstrap.py` | MISC | P1 | yes | scripts=1 ssot=1 other=1 | `_bootstrap.py:19` |
| `scripts/agent_org.py` | COORD | P1 | yes | packages=5 scripts=18 ssot=106 other=2 | `packages/script_pipeline/prompts/orchestrator_prompt.txt:18` |
| `scripts/agent_runner.py` | COORD | P1 | yes | packages=8 scripts=14 ssot=32 | `packages/factory_common/agent_mode.py:272` |
| `scripts/aggregate_llm_usage.py` | MISC | P1 | yes | ssot=6 | `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md:461` |
| `scripts/aggregate_voicevox_reading_logs.py` | AUDIO | P1 | yes | scripts=1 ssot=2 | `scripts/aggregate_voicevox_reading_logs.py:6` |
| `scripts/api_health_check.py` | MISC | P1 | yes | apps=2 ssot=4 other=2 | `apps/ui-backend/tools/start_manager.py:564` |
| `scripts/audio_integrity_report.py` | AUDIO | P1 | yes | ssot=2 | `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md:462` |
| `scripts/audit_alignment_semantic.py` | SCRIPT | P0 | yes | scripts=2 ssot=3 other=1 | `scripts/audit_alignment_semantic.py:13` |
| `scripts/batch_regenerate_tts.py` | AUDIO | P1 | yes | ssot=3 | `ssot/ops/OPS_ENTRYPOINTS_INDEX.md:54` |
| `scripts/buddha_senior_5ch_generate_scripts.py` | MISC | P1 | yes | ssot=2 | `ssot/ops/OPS_ENTRYPOINTS_INDEX.md:81` |
| `scripts/buddha_senior_5ch_prepare.py` | MISC | P1 | yes | ssot=2 | `ssot/ops/OPS_ENTRYPOINTS_INDEX.md:80` |
| `scripts/build_video_payload.py` | VIDEO | P1 | yes | ssot=1 | `ssot/ops/OPS_SCRIPTS_PHASE_CLASSIFICATION.md:188` |
| `scripts/ch01/check_script.py` | MISC | P1 | yes | scripts=1 ssot=2 | `scripts/ch01/check_script.py:5` |
| `scripts/ch01/generate_prompt_input.py` | MISC | P1 | yes | scripts=2 ssot=2 | `scripts/ch01/generate_prompt_input.py:5` |
| `scripts/check_all_srt.sh` | AUDIO | P1 | yes | scripts=2 ssot=3 | `scripts/check_all_srt.sh:14` |
| `scripts/check_env.py` | MISC | P1 | yes | apps=3 scripts=1 ssot=4 other=2 | `apps/ui-backend/backend/main.py:666` |
| `scripts/cleanup_audio_prep.py` | AUDIO | P1 | yes | scripts=2 ssot=6 | `scripts/cleanup_audio_prep.py:11` |
| `scripts/cleanup_data.py` | OPS | P1 | yes | ssot=15 | `ssot/agent_runbooks/RUNBOOK_CLEANUP_DATA.md:10` |
| `scripts/cleanup_workspace.py` | OPS | P1 | yes | packages=2 ssot=26 other=1 | `packages/script_pipeline/prompts/phase2_audio_prompt.txt:57` |
| `scripts/drive_oauth_setup.py` | PUBLISH | P1 | yes | scripts=3 ssot=1 README=2 | `README.md:53` |
| `scripts/drive_upload_oauth.py` | PUBLISH | P1 | yes | scripts=1 ssot=2 README=1 | `README.md:60` |
| `scripts/e2e_smoke.sh` | MISC | P1 | yes | ssot=1 | `ssot/ops/OPS_SCRIPTS_PHASE_CLASSIFICATION.md:243` |
| `scripts/enforce_alignment.py` | SCRIPT | P0 | yes | packages=1 ssot=7 | `packages/audio_tts/scripts/run_tts.py:366` |
| `scripts/episode_ssot.py` | SCRIPT | P1 | yes | packages=2 scripts=3 ssot=9 other=2 | `packages/audio_tts/scripts/run_tts.py:123` |
| `scripts/expand_a_text.py` | SCRIPT | P1 | yes | ssot=3 | `ssot/ops/OPS_ENTRYPOINTS_INDEX.md:133` |
| `scripts/force_asset_sync.py` | MISC | P1 | yes | apps=2 ssot=1 | `apps/ui-backend/tools/start_manager.py:608` |
| `scripts/format_srt_linebreaks.py` | AUDIO | P1 | yes | scripts=2 ssot=5 | `scripts/format_srt_linebreaks.py:14` |
| `scripts/generate_subtitles.py` | AUDIO | P1 | yes | scripts=1 ssot=1 | `scripts/generate_subtitles.py:7` |
| `scripts/image_usage_report.py` | VIDEO | P1 | yes | ssot=2 | `ssot/ops/OPS_LOGGING_MAP.md:81` |
| `scripts/list_redo.py` | MISC | P1 | yes | ssot=3 | `ssot/ops/OPS_SCRIPTS_PHASE_CLASSIFICATION.md:118` |
| `scripts/llm_logs_combined_report.py` | MISC | P1 | yes | ssot=4 | `ssot/ops/OPS_LOGGING_MAP.md:100` |
| `scripts/llm_provenance_report.py` | MISC | P1 | yes | scripts=2 ssot=1 | `scripts/llm_provenance_report.py:14` |
| `scripts/llm_usage_report.py` | MISC | P1 | yes | ssot=3 | `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md:461` |
| `scripts/mark_redo_done.py` | MISC | P1 | yes | scripts=2 ssot=2 | `scripts/mark_redo_done.py:5` |
| `scripts/notifications.py` | MISC | P1 | yes | ssot=2 | `ssot/agent_runbooks/RUNBOOK_JOB_RUNNER_DAEMON.md:71` |
| `scripts/openrouter_caption_probe.py` | MISC | P1 | yes | apps=1 ssot=1 | `apps/ui-backend/tools/start_manager.py:600` |
| `scripts/openrouter_key_probe.py` | MISC | P1 | yes | apps=2 ssot=1 | `apps/ui-backend/tools/start_manager.py:594` |
| `scripts/ops/_bootstrap.py` | OPS | P1 | yes | ssot=1 | `ssot/ops/OPS_SCRIPTS_PHASE_CLASSIFICATION.md:185` |
| `scripts/ops/a_text_lint.py` | OPS | P0 | yes | scripts=2 ssot=7 | `scripts/ops/a_text_lint.py:13` |
| `scripts/ops/a_text_marathon_compose.py` | OPS | P0 | yes | ssot=23 | `ssot/ops/OPS_A_TEXT_LLM_QUALITY_GATE.md:136` |
| `scripts/ops/a_text_section_compose.py` | OPS | P0 | yes | scripts=2 ssot=9 | `scripts/ops/a_text_section_compose.py:19` |
| `scripts/ops/agent_bootstrap.py` | OPS | P1 | no | scripts=2 ssot=4 | `scripts/ops/orchestrator_bootstrap.py:64` |
| `scripts/ops/archive_capcut_local_drafts.py` | OPS | P1 | yes | ssot=4 other=1 | `ssot/ops/OPS_ENTRYPOINTS_INDEX.md:235` |
| `scripts/ops/archive_published_episodes.py` | OPS | P1 | yes | ssot=4 | `ssot/ops/OPS_ARCHIVE_PUBLISHED_EPISODES.md:12` |
| `scripts/ops/archive_thumbnails_legacy_channel_dirs.py` | OPS | P1 | yes | ssot=2 | `ssot/ops/OPS_ENTRYPOINTS_INDEX.md:236` |
| `scripts/ops/channel_info_normalize.py` | OPS | P1 | yes | ssot=9 | `ssot/ops/OPS_CHANNEL_BENCHMARKS.md:97` |
| `scripts/ops/cleanup_broken_symlinks.py` | OPS | P1 | yes | ssot=5 | `ssot/ops/OPS_ENTRYPOINTS_INDEX.md:231` |
| `scripts/ops/cleanup_caches.sh` | OPS | P1 | yes | ssot=3 | `ssot/ops/OPS_AGENT_PLAYBOOK.md:217` |
| `scripts/ops/cleanup_logs.py` | OPS | P1 | yes | ssot=10 other=1 | `ssot/OPS_SYSTEM_OVERVIEW.md:200` |
| `scripts/ops/cleanup_remotion_artifacts.py` | OPS | P1 | yes | ssot=2 | `ssot/ops/OPS_ENTRYPOINTS_INDEX.md:232` |
| `scripts/ops/cleanup_video_runs.py` | OPS | P1 | yes | scripts=1 ssot=3 | `scripts/ops/restore_video_runs.py:6` |
| `scripts/ops/docs_inventory.py` | OPS | P1 | yes | scripts=2 ssot=2 | `scripts/ops/docs_inventory.py:14` |
| `scripts/ops/episode_progress.py` | OPS | P1 | no | scripts=5 ssot=3 | `scripts/ops/episode_progress.py:12` |
| `scripts/ops/fact_check_codex.py` | OPS | P1 | no | ssot=2 | `ssot/ops/OPS_ENTRYPOINTS_INDEX.md:73` |
| `scripts/ops/git_write_lock.py` | OPS | P1 | no | scripts=9 ssot=8 | `scripts/ops/git_write_lock.py:19` |
| `scripts/ops/idea.py` | OPS | P1 | yes | scripts=5 ssot=14 other=1 | `scripts/ops/idea.py:19` |
| `scripts/ops/import_ch01_legacy_scripts.py` | OPS | P1 | yes | scripts=1 ssot=1 | `scripts/ops/import_ch01_legacy_scripts.py:13` |
| `scripts/ops/init_workspaces.py` | OPS | P1 | yes | scripts=2 ssot=1 | `scripts/ops/init_workspaces.py:11` |
| `scripts/ops/lint_llm_config.py` | OPS | P1 | no | refs=0 | `-` |
| `scripts/ops/llm_hardcode_audit.py` | OPS | P1 | no | scripts=1 | `scripts/ops/pre_push_final_check.py:50` |
| `scripts/ops/llm_usage_report.py` | OPS | P1 | yes | ssot=2 | `ssot/ops/OPS_ENV_VARS.md:25` |
| `scripts/ops/logs_snapshot.py` | OPS | P1 | yes | ssot=4 | `ssot/ops/OPS_ENTRYPOINTS_INDEX.md:229` |
| `scripts/ops/orchestrator_bootstrap.py` | OPS | P1 | no | ssot=2 | `ssot/ops/OPS_ENTRYPOINTS_INDEX.md:158` |
| `scripts/ops/pages_script_viewer_index.py` | OPS | P1 | no | scripts=3 ssot=2 other=2 | `docs/README.md:14` |
| `scripts/ops/pages_snapshot_export.py` | OPS | P1 | no | scripts=2 other=3 | `docs/data/snapshot/channels.json:4` |
| `scripts/ops/parallel_ops_preflight.py` | OPS | P1 | no | scripts=1 ssot=2 | `scripts/ops/orchestrator_bootstrap.py:86` |
| `scripts/ops/planning_apply_patch.py` | OPS | P1 | yes | scripts=3 ssot=8 other=5 | `scripts/ops/planning_apply_patch.py:14` |
| `scripts/ops/planning_lint.py` | OPS | P0 | yes | apps=1 scripts=9 ssot=21 other=9 | `apps/ui-backend/backend/main.py:3156` |
| `scripts/ops/planning_patch_gen.py` | OPS | P1 | yes | ssot=3 other=3 | `ssot/ops/OPS_ENTRYPOINTS_INDEX.md:92` |
| `scripts/ops/planning_realign_to_title.py` | OPS | P1 | yes | scripts=3 ssot=5 | `scripts/ops/planning_realign_to_title.py:31` |
| `scripts/ops/planning_sanitize.py` | OPS | P0 | yes | scripts=3 ssot=6 | `scripts/ops/planning_sanitize.py:19` |
| `scripts/ops/pre_push_final_check.py` | OPS | P1 | no | scripts=3 ssot=2 | `scripts/ops/pre_push_final_check.py:9` |
| `scripts/ops/preproduction_audit.py` | OPS | P1 | yes | scripts=15 ssot=11 other=1 | `scripts/ops/preproduction_audit.py:11` |
| `scripts/ops/preproduction_issue_catalog.py` | OPS | P1 | yes | ssot=1 other=1 | `ssot/ops/OPS_SCRIPTS_PHASE_CLASSIFICATION.md:138` |
| `scripts/ops/production_pack.py` | OPS | P1 | yes | scripts=8 ssot=10 other=3 | `scripts/ops/preproduction_issue_catalog.py:64` |
| `scripts/ops/prompts_inventory.py` | OPS | P1 | yes | scripts=3 ssot=2 other=1 | `prompts/PROMPTS_INDEX.md:3` |
| `scripts/ops/prune_video_run_legacy_files.py` | OPS | P1 | yes | ssot=2 | `ssot/ops/OPS_ENTRYPOINTS_INDEX.md:233` |
| `scripts/ops/publish_lock_cli.py` | OPS | P1 | yes | ssot=2 | `ssot/ops/OPS_PLANNING_CSV_WORKFLOW.md:187` |
| `scripts/ops/purge_legacy_agent_task_queues.py` | OPS | P1 | yes | ssot=2 | `ssot/ops/OPS_ENTRYPOINTS_INDEX.md:237` |
| `scripts/ops/render_remotion_batch.py` | OPS | P0 | yes | ssot=5 | `ssot/OPS_SYSTEM_OVERVIEW.md:147` |
| `scripts/ops/repo_ref_audit.py` | OPS | P1 | yes | scripts=4 ssot=3 | `scripts/ops/repo_ref_audit.py:11` |
| `scripts/ops/repo_sanity_audit.py` | OPS | P1 | yes | scripts=3 ssot=4 | `scripts/ops/pre_push_final_check.py:41` |
| `scripts/ops/research_genre_index.py` | OPS | P1 | yes | scripts=5 ssot=5 other=15 | `scripts/ops/research_genre_index.py:17` |
| `scripts/ops/restore_video_runs.py` | OPS | P1 | yes | ssot=6 other=1 | `ssot/ops/OPS_ENTRYPOINTS_INDEX.md:222` |
| `scripts/ops/save_patch.sh` | OPS | P1 | yes | scripts=1 ssot=4 other=1 | `AGENTS.md:28` |
| `scripts/ops/script_runbook.py` | OPS | P0 | yes | scripts=6 ssot=65 | `scripts/ops/script_runbook.py:15` |
| `scripts/ops/scripts_inventory.py` | OPS | P1 | yes | scripts=3 ssot=3 | `scripts/ops/scripts_inventory.py:11` |
| `scripts/ops/ssot_audit.py` | OPS | P1 | yes | scripts=2 ssot=6 | `scripts/ops/docs_inventory.py:10` |
| `scripts/ops/yt_dlp_benchmark_analyze.py` | OPS | P1 | no | apps=1 scripts=5 ssot=2 other=1 | `apps/ui-frontend/src/pages/BenchmarksPage.tsx:1424` |
| `scripts/ops/yt_dlp_thumbnail_analyze.py` | OPS | P1 | no | apps=1 scripts=2 | `apps/ui-frontend/src/pages/BenchmarksPage.tsx:1325` |
| `scripts/prompt_audit.py` | OPS | P1 | yes | apps=2 ssot=2 | `apps/ui-backend/tools/start_manager.py:582` |
| `scripts/purge_audio_final_chunks.py` | AUDIO | P1 | yes | scripts=2 ssot=4 | `scripts/purge_audio_final_chunks.py:11` |
| `scripts/purge_audio_prep_binaries.py` | AUDIO | P1 | yes | ssot=4 | `ssot/ops/OPS_AUDIO_TTS.md:96` |
| `scripts/py` | MISC | P1 | no | refs=0 | `-` |
| `scripts/remotion_export.py` | VIDEO | P1 | yes | apps=2 ssot=1 | `apps/ui-frontend/src/components/RemotionWorkspace.tsx:1212` |
| `scripts/repair_manager.py` | MISC | P1 | yes | packages=2 scripts=1 ssot=2 | `packages/audio_tts/docs/SRT_SYNC_PROTOCOL.md:36` |
| `scripts/run_srt2images.sh` | AUDIO | P1 | yes | packages=1 ssot=1 | `packages/video_pipeline/server/jobs.py:737` |
| `scripts/sanitize_a_text.py` | SCRIPT | P1 | yes | packages=1 ssot=5 | `packages/script_pipeline/runner.py:8026` |
| `scripts/start_all.sh` | MISC | P0 | yes | apps=3 ssot=7 other=1 | `apps/ui-backend/backend/__init__.py:5` |
| `scripts/sync_all_scripts.py` | MISC | P1 | yes | scripts=2 ssot=2 | `scripts/start_all.sh:169` |
| `scripts/sync_audio_prep_to_final.py` | AUDIO | P1 | yes | scripts=2 ssot=4 | `scripts/sync_audio_prep_to_final.py:15` |
| `scripts/sync_thumbnail_inventory.py` | THUMB | P0 | yes | apps=2 ssot=1 | `apps/ui-backend/tools/start_manager.py:616` |
| `scripts/think.sh` | MISC | P1 | yes | scripts=4 ssot=11 | `scripts/think.sh:7` |
| `scripts/thumbnails/build.py` | MISC | P0 | yes | apps=1 ssot=17 other=5 | `apps/ui-frontend/src/components/ThumbnailWorkspace.tsx:3057` |
| `scripts/thumbnails/ch26_make_two_variants.py` | MISC | P1 | no | ssot=4 | `ssot/plans/PLAN_OPS_PERFORMANCE_BOTTLENECKS.md:16` |
| `scripts/thumbnails/gen_buddha_channel_bases.py` | MISC | P1 | no | refs=0 | `-` |
| `scripts/thumbnails/portraits_wikimedia.py` | MISC | P1 | no | refs=0 | `-` |
| `scripts/validate_status_sweep.py` | MISC | P1 | yes | apps=2 ssot=2 | `apps/ui-backend/tools/start_manager.py:572` |
| `scripts/verify_srt_sync.py` | AUDIO | P1 | yes | scripts=2 ssot=2 | `scripts/check_all_srt.sh:34` |
| `scripts/with_agent_mode.sh` | COORD | P1 | yes | scripts=3 ssot=1 | `scripts/with_agent_mode.sh:3` |
| `scripts/with_ytm_env.sh` | MISC | P0 | yes | apps=1 packages=2 scripts=13 ssot=134 | `apps/remotion/REMOTION_PLAN.md:15` |
| `scripts/youtube_publisher/README.md` | PUBLISH | P1 | yes | ssot=1 README=1 | `README.md:69` |
| `scripts/youtube_publisher/oauth_setup.py` | PUBLISH | P1 | yes | scripts=1 ssot=1 README=1 | `README.md:66` |
| `scripts/youtube_publisher/publish_from_sheet.py` | PUBLISH | P0 | yes | scripts=2 ssot=7 README=1 | `README.md:67` |

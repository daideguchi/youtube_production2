# OPS_SCRIPTS_INVENTORY — scripts/ 全ファイル棚卸し（工程別 / 使う・使わない）

生成:
- `python3 scripts/ops/scripts_inventory.py --write`

目的:
- `scripts/**` を **全量**列挙し、工程（Phase）と分類（P0/P1/P2/P3）を 1 行ずつ確定する。
- ゴミ判定ミス（例: `run_srt2images.sh` のような間接呼び出し）を防ぐため、ref（参照元）も併記する。

正本:
- フロー: `ssot/OPS_CONFIRMED_PIPELINE_FLOW.md`
- 入口/方針: `ssot/OPS_SCRIPTS_PHASE_CLASSIFICATION.md`

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
| `scripts/_adhoc/README.md` | MISC | P3 | yes | other=1 | `workspaces/logs/agent_tasks/coordination/events.jsonl:1176` |
| `scripts/_bootstrap.py` | MISC | P1 | yes | scripts=1 ssot=2 other=1 | `_bootstrap.py:19` |
| `scripts/agent_coord.py` | COORD | P1 | yes | scripts=3 ssot=7 other=8 | `scripts/agent_coord.py:5` |
| `scripts/agent_org.py` | COORD | P1 | yes | packages=5 scripts=8 ssot=85 other=44 | `packages/script_pipeline/prompts/orchestrator_prompt.txt:18` |
| `scripts/agent_runner.py` | COORD | P1 | yes | packages=8 scripts=14 ssot=32 other=103 | `packages/factory_common/agent_mode.py:272` |
| `scripts/aggregate_llm_usage.py` | MISC | P1 | yes | ssot=7 other=5 | `ssot/OPS_CONFIRMED_PIPELINE_FLOW.md:409` |
| `scripts/aggregate_voicevox_reading_logs.py` | AUDIO | P1 | yes | scripts=1 ssot=2 other=5 | `scripts/aggregate_voicevox_reading_logs.py:6` |
| `scripts/api_health_check.py` | MISC | P1 | yes | apps=2 ssot=5 other=8 | `apps/ui-backend/tools/start_manager.py:465` |
| `scripts/audio_integrity_report.py` | AUDIO | P1 | yes | ssot=3 other=6 | `ssot/OPS_CONFIRMED_PIPELINE_FLOW.md:410` |
| `scripts/audit_alignment_semantic.py` | SCRIPT | P0 | yes | scripts=4 ssot=2 other=18 | `scripts/audit_alignment_semantic.py:13` |
| `scripts/batch_regenerate_tts.py` | AUDIO | P1 | yes | ssot=3 other=6 | `ssot/OPS_ENTRYPOINTS_INDEX.md:44` |
| `scripts/buddha_senior_5ch_generate_scripts.py` | MISC | P1 | yes | ssot=2 other=9 | `ssot/OPS_ENTRYPOINTS_INDEX.md:58` |
| `scripts/buddha_senior_5ch_prepare.py` | MISC | P1 | yes | ssot=4 other=10 | `ssot/OPS_ENTRYPOINTS_INDEX.md:57` |
| `scripts/build_video_payload.py` | VIDEO | P1 | yes | ssot=1 other=6 | `ssot/OPS_SCRIPTS_PHASE_CLASSIFICATION.md:154` |
| `scripts/check_all_srt.sh` | AUDIO | P1 | yes | scripts=2 ssot=4 other=6 | `scripts/check_all_srt.sh:14` |
| `scripts/check_env.py` | MISC | P1 | yes | apps=3 scripts=3 ssot=7 other=9 | `apps/ui-backend/backend/main.py:652` |
| `scripts/cleanup_audio_prep.py` | AUDIO | P1 | yes | scripts=2 ssot=5 other=5 | `scripts/cleanup_audio_prep.py:11` |
| `scripts/cleanup_data.py` | OPS | P1 | yes | ssot=17 other=6 | `ssot/OPS_CLEANUP_EXECUTION_LOG.md:182` |
| `scripts/cleanup_workspace.py` | OPS | P1 | yes | packages=2 ssot=45 other=12 | `packages/script_pipeline/prompts/phase2_audio_prompt.txt:57` |
| `scripts/drive_oauth_setup.py` | PUBLISH | P1 | yes | scripts=3 ssot=2 README=2 other=7 | `README.md:54` |
| `scripts/drive_upload_oauth.py` | PUBLISH | P1 | yes | scripts=1 ssot=2 README=1 other=5 | `README.md:61` |
| `scripts/e2e_smoke.sh` | MISC | P1 | yes | ssot=2 other=5 | `ssot/OPS_SCRIPTS_PHASE_CLASSIFICATION.md:203` |
| `scripts/enforce_alignment.py` | SCRIPT | P0 | yes | packages=1 ssot=8 other=13 | `packages/audio_tts_v2/scripts/run_tts.py:183` |
| `scripts/episode_ssot.py` | SCRIPT | P1 | yes | scripts=1 ssot=19 other=11 | `scripts/ops/restore_video_runs.py:7` |
| `scripts/expand_a_text.py` | SCRIPT | P1 | yes | ssot=4 other=13 | `ssot/OPS_ENTRYPOINTS_INDEX.md:83` |
| `scripts/force_asset_sync.py` | MISC | P1 | yes | apps=2 packages=1 ssot=2 other=7 | `apps/ui-backend/tools/start_manager.py:509` |
| `scripts/generate_subtitles.py` | AUDIO | P1 | yes | scripts=1 ssot=1 other=6 | `scripts/generate_subtitles.py:7` |
| `scripts/image_usage_report.py` | VIDEO | P1 | yes | ssot=2 other=5 | `ssot/OPS_LOGGING_MAP.md:81` |
| `scripts/list_redo.py` | MISC | P1 | yes | ssot=3 other=5 | `ssot/OPS_SCRIPTS_PHASE_CLASSIFICATION.md:117` |
| `scripts/llm_logs_combined_report.py` | MISC | P1 | yes | ssot=4 other=5 | `ssot/OPS_LOGGING_MAP.md:100` |
| `scripts/llm_provenance_report.py` | MISC | P1 | yes | scripts=2 ssot=1 other=7 | `scripts/llm_provenance_report.py:14` |
| `scripts/llm_usage_report.py` | MISC | P1 | yes | ssot=3 other=5 | `ssot/OPS_CONFIRMED_PIPELINE_FLOW.md:409` |
| `scripts/mark_redo_done.py` | MISC | P1 | yes | scripts=2 ssot=3 other=5 | `scripts/mark_redo_done.py:5` |
| `scripts/notifications.py` | MISC | P1 | yes | ssot=2 other=5 | `ssot/OPS_SCRIPTS_PHASE_CLASSIFICATION.md:146` |
| `scripts/openrouter_caption_probe.py` | MISC | P1 | yes | apps=1 ssot=1 other=5 | `apps/ui-backend/tools/start_manager.py:501` |
| `scripts/openrouter_key_probe.py` | MISC | P1 | yes | apps=2 ssot=1 other=1 | `apps/ui-backend/tools/start_manager.py:495` |
| `scripts/ops/_bootstrap.py` | OPS | P1 | yes | ssot=1 | `ssot/OPS_SCRIPTS_PHASE_CLASSIFICATION.md:151` |
| `scripts/ops/a_text_lint.py` | OPS | P0 | yes | scripts=2 ssot=7 other=4 | `scripts/ops/a_text_lint.py:13` |
| `scripts/ops/a_text_marathon_compose.py` | OPS | P0 | yes | ssot=18 other=8 | `ssot/OPS_CONFIRMED_PIPELINE_FLOW.md:116` |
| `scripts/ops/a_text_section_compose.py` | OPS | P0 | yes | scripts=2 ssot=8 other=11 | `scripts/ops/a_text_section_compose.py:19` |
| `scripts/ops/archive_capcut_local_drafts.py` | OPS | P1 | yes | ssot=6 other=2 | `ssot/OPS_CLEANUP_EXECUTION_LOG.md:1070` |
| `scripts/ops/archive_thumbnails_legacy_channel_dirs.py` | OPS | P1 | yes | ssot=4 | `ssot/OPS_CLEANUP_EXECUTION_LOG.md:1035` |
| `scripts/ops/cleanup_broken_symlinks.py` | OPS | P1 | yes | ssot=15 other=4 | `ssot/OPS_CLEANUP_EXECUTION_LOG.md:948` |
| `scripts/ops/cleanup_caches.sh` | OPS | P1 | yes | ssot=5 other=2 | `ssot/OPS_AGENT_PLAYBOOK.md:165` |
| `scripts/ops/cleanup_logs.py` | OPS | P1 | yes | ssot=9 other=3 | `ssot/OPS_ENTRYPOINTS_INDEX.md:157` |
| `scripts/ops/cleanup_remotion_artifacts.py` | OPS | P1 | yes | ssot=4 other=2 | `ssot/OPS_CLEANUP_EXECUTION_LOG.md:1010` |
| `scripts/ops/cleanup_video_runs.py` | OPS | P1 | yes | scripts=1 ssot=4 other=1 | `scripts/ops/restore_video_runs.py:6` |
| `scripts/ops/logs_snapshot.py` | OPS | P1 | yes | ssot=4 | `ssot/OPS_ENTRYPOINTS_INDEX.md:158` |
| `scripts/ops/planning_lint.py` | OPS | P0 | yes | scripts=3 ssot=9 other=5 | `scripts/ops/planning_lint.py:12` |
| `scripts/ops/planning_sanitize.py` | OPS | P0 | yes | scripts=2 ssot=2 other=2 | `scripts/ops/planning_sanitize.py:19` |
| `scripts/ops/prune_video_run_legacy_files.py` | OPS | P1 | yes | ssot=7 other=4 | `ssot/OPS_CLEANUP_EXECUTION_LOG.md:997` |
| `scripts/ops/purge_legacy_agent_task_queues.py` | OPS | P1 | yes | ssot=4 other=1 | `ssot/OPS_CLEANUP_EXECUTION_LOG.md:1048` |
| `scripts/ops/render_remotion_batch.py` | OPS | P0 | yes | ssot=4 other=18 | `ssot/OPS_AGENT_PLAYBOOK.md:92` |
| `scripts/ops/restore_video_runs.py` | OPS | P1 | yes | ssot=7 other=1 | `ssot/OPS_ENTRYPOINTS_INDEX.md:151` |
| `scripts/ops/save_patch.sh` | OPS | P1 | yes | scripts=1 ssot=2 other=2 | `AGENTS.md:28` |
| `scripts/ops/scripts_inventory.py` | OPS | P1 | yes | scripts=3 ssot=4 other=2 | `scripts/ops/scripts_inventory.py:11` |
| `scripts/ops/ssot_audit.py` | OPS | P1 | yes | ssot=7 other=2 | `ssot/OPS_ENTRYPOINTS_INDEX.md:171` |
| `scripts/ops/stage2_cutover_workspaces.py` | OPS | P1 | yes | ssot=6 other=6 | `ssot/OPS_SCRIPTS_PHASE_CLASSIFICATION.md:190` |
| `scripts/prompt_audit.py` | OPS | P1 | yes | apps=2 ssot=5 other=7 | `apps/ui-backend/tools/start_manager.py:483` |
| `scripts/purge_audio_final_chunks.py` | AUDIO | P1 | yes | scripts=2 ssot=6 other=5 | `scripts/purge_audio_final_chunks.py:11` |
| `scripts/purge_audio_prep_binaries.py` | AUDIO | P1 | yes | ssot=7 other=6 | `ssot/OPS_AUDIO_TTS_V2.md:88` |
| `scripts/remotion_export.py` | VIDEO | P1 | yes | apps=2 ssot=1 other=5 | `apps/ui-frontend/src/components/RemotionWorkspace.tsx:1186` |
| `scripts/repair_manager.py` | MISC | P1 | yes | packages=2 scripts=1 ssot=3 other=5 | `packages/audio_tts_v2/docs/SRT_SYNC_PROTOCOL.md:36` |
| `scripts/run_srt2images.sh` | AUDIO | P1 | yes | packages=1 ssot=1 other=5 | `packages/commentary_02_srt2images_timeline/server/jobs.py:664` |
| `scripts/sanitize_a_text.py` | SCRIPT | P1 | yes | packages=2 ssot=7 other=38 | `packages/audio_tts_v2/README.md:27` |
| `scripts/sitecustomize.py` | MISC | P1 | yes | ssot=1 other=5 | `ssot/OPS_SCRIPTS_PHASE_CLASSIFICATION.md:149` |
| `scripts/start_all.sh` | MISC | P0 | yes | apps=1 packages=1 ssot=13 other=13 | `apps/ui-backend/tools/start_manager.py:5` |
| `scripts/sync_all_scripts.py` | MISC | P1 | yes | scripts=2 ssot=3 other=15 | `scripts/start_all.sh:133` |
| `scripts/sync_audio_prep_to_final.py` | AUDIO | P1 | yes | scripts=2 ssot=4 other=6 | `scripts/sync_audio_prep_to_final.py:15` |
| `scripts/sync_ch02_scripts.py` | MISC | P1 | yes | ssot=3 other=13 | `ssot/OPS_SCRIPTS_PHASE_CLASSIFICATION.md:200` |
| `scripts/sync_thumbnail_inventory.py` | THUMB | P0 | yes | apps=2 ssot=1 other=5 | `apps/ui-backend/tools/start_manager.py:517` |
| `scripts/think.sh` | MISC | P1 | yes | scripts=4 ssot=11 other=5 | `scripts/think.sh:7` |
| `scripts/validate_status_sweep.py` | MISC | P1 | yes | apps=2 packages=1 ssot=5 other=8 | `apps/ui-backend/tools/start_manager.py:473` |
| `scripts/verify_srt_sync.py` | AUDIO | P1 | yes | scripts=2 ssot=3 other=6 | `scripts/check_all_srt.sh:34` |
| `scripts/with_agent_mode.sh` | COORD | P1 | yes | scripts=3 ssot=1 other=5 | `scripts/with_agent_mode.sh:3` |
| `scripts/with_ytm_env.sh` | MISC | P1 | yes | apps=1 packages=3 scripts=7 ssot=5 other=6 | `apps/remotion/REMOTION_PLAN.md:15` |
| `scripts/youtube_publisher/README.md` | PUBLISH | P1 | yes | ssot=2 README=1 other=1 | `README.md:70` |
| `scripts/youtube_publisher/oauth_setup.py` | PUBLISH | P1 | yes | scripts=1 ssot=2 README=1 other=2 | `README.md:67` |
| `scripts/youtube_publisher/publish_from_sheet.py` | PUBLISH | P0 | yes | scripts=2 ssot=6 README=1 | `README.md:68` |

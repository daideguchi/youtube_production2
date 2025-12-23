# OPS_SCRIPTS_PHASE_CLASSIFICATION — 工程別「使う/使わない」スクリプト確定表（SSOT）

目的:
- `scripts/` と `scripts/ops/` が散らかっても、**低知能エージェントでも迷わず**同じ入口を叩けるようにする。
- 「絶対に使う（正規入口）」「絶対に使わない（禁止/削除対象）」「一時（adhoc）」を工程別に確定し、誤実行を防ぐ。

正本フロー: `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`  
入口索引（実行コマンドの一覧）: `ssot/ops/OPS_ENTRYPOINTS_INDEX.md`
全ファイル棚卸し（scripts/ 全量）: `ssot/ops/OPS_SCRIPTS_INVENTORY.md`

---

## 0. 分類（このSSOTで確定）

### P0: 正規入口（絶対に使う）
通常運用で「まず叩く」入口。工程の正本は **P0 だけ**とする。

### P1: 付帯/診断（使うことはあるが主線ではない）
主線の補助・監査・復旧・ヘルスチェック。  
実行はOKだが、P0の代替として使わない（主線を壊しやすい）。

### P2: 禁止（絶対に使わない）
誤誘導・旧設計・品質事故の温床。**実行禁止**。  
原則「archive-first → 削除」を次のcleanupバッチで行う。

### P3: 一時スクリプト（adhoc）
その場限りの検証/一時バッチ。**置き場を固定**して混入を防ぐ（後述）。

---

## 1. 置き場ルール（固定ロジック）

### 1.1 正規入口の置き場
- P0/P1 は `scripts/` または `scripts/ops/` のみ。
- P0/P1 を追加/変更したら、必ず `ssot/ops/OPS_ENTRYPOINTS_INDEX.md` と本書を更新する。

### 1.2 一時スクリプト（P3）の置き場
- 置き場: `scripts/_adhoc/`
- 原則 `.gitignore` で除外（混入防止）。必要があれば「期限付きで」明示的に add する。
- ファイル先頭に必ずメタ情報を書く（テンプレ）:

```
#!/usr/bin/env python3
"""
adhoc: <目的>
owner: <agent/person>
created: YYYY-MM-DD
expires: YYYY-MM-DD
notes: <消し忘れ防止の一言>
"""
```

### 1.3 禁止スクリプト（P2）の扱い
- 参照/依存が0であることを確認してから、`backups/graveyard/` に archive-first → repoから削除。
- 証跡は `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md` に残す（復元可能性を担保）。

---

## 2. 工程別「絶対に使う」入口（P0）

### Phase A. Planning（企画）
- P0:
  - Planning SoT更新: `workspaces/planning/channels/CHxx.csv`（UI `/progress` でも可）
  - 汚染/欠落検知（決定論）: `python3 scripts/ops/planning_lint.py --csv workspaces/planning/channels/CHxx.csv --write-latest`
  - L3混入クリーナ（決定論・保守）: `python3 scripts/ops/planning_sanitize.py --channel CHxx --write-latest`（dry-run）→ 必要時のみ `--apply`

### Phase B. Script Pipeline（台本生成）
- P0:
  - 生成主線: `python -m script_pipeline.cli next/run-all --channel CHxx --video NNN`
  - 長尺（セクション分割）: `python3 scripts/ops/a_text_section_compose.py --channel CHxx --video NNN --apply --run-validation`
  - 超長尺（Marathon）: `python3 scripts/ops/a_text_marathon_compose.py --channel CHxx --video NNN --duration-minutes 120 --apply`
  - Aテキストlint（決定論）: `python3 scripts/ops/a_text_lint.py --channel CHxx --video NNN --write-latest`
  - 整合スタンプ再付与（決定論）: `python3 scripts/enforce_alignment.py --channels CHxx --apply`
  - 品質/整合の監査（read-only）: `python3 scripts/audit_alignment_semantic.py --channels CHxx --videos NNN`

### Phase C. Audio / TTS（音声・SRT）
- P0:
  - 正規: `python -m script_pipeline.cli audio --channel CHxx --video NNN`
  - 直叩き（必要時）: `PYTHONPATH=\".:packages\" python3 -m audio_tts_v2.scripts.run_tts --channel CHxx --video NNN --input workspaces/scripts/CHxx/NNN/content/assembled.md`

### Phase D. Video（SRT→画像→CapCut）
- P0:
  - 正規: `PYTHONPATH=\".:packages\" python3 -m commentary_02_srt2images_timeline.tools.factory ...`
  - 詳細制御: `PYTHONPATH=\".:packages\" python3 -m commentary_02_srt2images_timeline.tools.auto_capcut_run --channel CHxx --srt <srt> --out workspaces/video/runs/<run_id> ...`

### Phase D'. Remotion（未主線/実験）
- P0（運用上の入口として固定）:
  - バッチ再レンダ: `python3 scripts/ops/render_remotion_batch.py --help`

### Phase E. Thumbnails（独立動線）
- P0:
  - UI（推奨）: `/thumbnails`
  - inventory同期（整合）: `python3 scripts/sync_thumbnail_inventory.py`（通常は start_manager guard で check）

### Phase F. Publish（YouTube）
- P0:
  - 投稿: `python3 scripts/youtube_publisher/publish_from_sheet.py --max-rows 1 --run`

### UI（運用入口）
- P0:
  - 起動: `bash scripts/start_all.sh start`（内部で `apps/ui-backend/tools/start_manager.py start` を呼ぶ）
  - ヘルスチェック: `python3 apps/ui-backend/tools/start_manager.py healthcheck --with-guards`

---

## 3. 付帯/診断（P1）

### Coordination / Agent運用
- `python3 scripts/agent_org.py ...`（locks/board/memos）
- `python3 scripts/agent_runner.py ...`（pending/results の運用）
- `python3 scripts/agent_coord.py ...`（互換wrapper。旧コマンドを `agent_org.py` に転送）
- `bash scripts/think.sh -- <cmd>`（LLM_MODE=think の安全運用）
- `bash scripts/with_agent_mode.sh -- <cmd>`（LLM_MODE=agent の運用）
- `bash scripts/with_ytm_env.sh <cmd>`（`.env` を export してから実行。シェル/Node系に必須）

### Redo（リテイク）運用
- `python3 scripts/list_redo.py --type script|audio|all [--channel CHxx]`
- `python3 scripts/mark_redo_done.py --channel CHxx --videos NNN ... [--type audio|script|all]`
  - UI/redo API が正本だが、CLI が必要な場合はこの入口を使う（lock尊重の改善はTODO）。

### Script（補助/リカバリ）
- `python3 scripts/sanitize_a_text.py --channel CHxx --videos NNN --mode dry-run|run`（Aテキストから出典/URL等のメタ混入を退避→除去→同期）
- `python3 scripts/expand_a_text.py --channel CHxx --videos NNN ...`（字数救済の補助。主線は品質ゲート側を優先）
- `python3 scripts/episode_ssot.py --help`（エピソード/パターンSSOTの監査・同期）
- `python3 scripts/buddha_senior_5ch_prepare.py --help`（CH12–CH16 初期化/メタ補完の補助）
- `python3 scripts/buddha_senior_5ch_generate_scripts.py --help`（CH12–CH16 の一括生成（APIなし）補助）

### Health / Audit
- `python3 scripts/check_env.py --env-file .env`（start_all内でも実行）
- `python3 scripts/api_health_check.py --base-url http://127.0.0.1:8000`
- `python3 scripts/validate_status_sweep.py --repair-global`（壊れたstatusの補正）
- `python3 scripts/prompt_audit.py --skip-scripts`（promptのみ。`start_manager healthcheck --with-guards` の既定）
- `python3 scripts/prompt_audit.py`（prompt + assembled/sanitized を監査。重いので必要時のみ）
- `python3 scripts/llm_provenance_report.py --channel CHxx --video NNN`（どのprovider/modelで生成されたかの追跡）
- `python3 scripts/force_asset_sync.py --dry-run`（`asset/`=L0 を正として role assets の同期/差分検知）
- OpenRouter疎通:
  - `python3 scripts/openrouter_key_probe.py`
  - `python3 scripts/openrouter_caption_probe.py`

### Reports（集計/確認）
- `python3 scripts/aggregate_llm_usage.py`（LLM利用集計の簡易サマリ）
- `python3 scripts/llm_usage_report.py` / `python3 scripts/llm_logs_combined_report.py`（ログ集計の補助）
- `python3 scripts/image_usage_report.py`（画像生成の利用状況サマリ）
- `python3 scripts/audio_integrity_report.py`（final音声の整合/欠損チェック）
- `python3 scripts/aggregate_voicevox_reading_logs.py`（VOICEVOX読みログの集計）
- `python3 scripts/notifications.py`（Slack webhook の疎通/通知テスト）

### Bootstrap（内部依存・消さない）
- `scripts/sitecustomize.py`（`python3 scripts/foo.py` で repo-root を sys.path に載せ `.env` をロードするための bootstrap）
- `scripts/_bootstrap.py`（`python3 scripts/foo.py` から `packages/` を見えるようにする薄い bootstrap）
- `scripts/ops/_bootstrap.py`（`python3 scripts/ops/foo.py` 用 bootstrap。ops系ツールが `from _bootstrap import bootstrap` で依存）

### Video（補助）
- `python3 scripts/build_video_payload.py --project-id <run_id>`（run_dir から CapCut/Remotion 互換の payload を生成）
- `python3 scripts/remotion_export.py --help`（Remotion workspace のエクスポート補助）
- `python3 scripts/repair_manager.py --help`（SRT/Audio/Run の repair 補助。`ssot/ops/OPS_LOGGING_MAP.md` 参照）

### SRT（補助）
- `python3 scripts/generate_subtitles.py CHxx-NNN ...`（既存SRTのタイミングを保持して本文だけ差し替え）

### Audio（補助/バッチ）
- `python3 scripts/batch_regenerate_tts.py --help`（UIの batch-tts が内部で呼ぶ。手動運用は原則UIから）
- `python3 scripts/cleanup_audio_prep.py --dry-run` → OKなら `--run`（prepの不要chunk削除）
- `python3 scripts/sync_audio_prep_to_final.py --help`（prep→final の不足同期）
- `python3 scripts/purge_audio_prep_binaries.py --help`（prep の重複 wav/srt 削除）
- `python3 scripts/purge_audio_final_chunks.py --help`（final の chunks 削除）
- `python3 scripts/verify_srt_sync.py [CHxx]`（final WAV長 ↔ SRT終端 の大まか整合チェック）
- `bash scripts/check_all_srt.sh [CHxx]`（`verify_srt_sync.py` のログ出力wrapper）

### Cleanup / Restore（運用で使う）
- `python -m scripts.cleanup_workspace --dry-run ...` → OKなら `--run`（統合cleanup）
- `python3 scripts/cleanup_data.py --dry-run` → OKなら `--run`（workspaces/scripts中間物）
- `python3 scripts/ops/cleanup_logs.py --run`（logsローテ）
- `bash scripts/ops/cleanup_caches.sh`（pycache等）
- `python3 scripts/ops/restore_video_runs.py --report ...`（run復旧）
- `python3 scripts/ops/logs_snapshot.py`（logsの現状スナップショット: 件数/サイズ）
- `python3 scripts/ops/cleanup_broken_symlinks.py --run`（壊れたsymlink削除: 探索ノイズ低減）
- `python3 scripts/ops/cleanup_remotion_artifacts.py --run`（remotion生成物のローテ）
- `python3 scripts/ops/prune_video_run_legacy_files.py --run`（video runs内の *.legacy.* を prune）
- `python3 scripts/ops/archive_capcut_local_drafts.py --run`（capcutローカルドラフトを _archive へ移動）
- `python3 scripts/ops/archive_thumbnails_legacy_channel_dirs.py --run`（thumbnails旧dirを _archive へ移動）
- `python3 scripts/ops/purge_legacy_agent_task_queues.py --run`（旧agent task queue残骸を archive-first で削除）
- `python3 scripts/ops/cleanup_video_runs.py --dry-run` → OKなら `--run`（video run_dir を `_archive/` へ退避。`cleanup_workspace --video-runs` が内部で呼ぶ）
- `bash scripts/run_srt2images.sh ...`（UI内部が呼ぶ wrapper。単体実行は原則デバッグのみ）

### SSOTメンテ（固定ロジックの維持）
- `python3 scripts/ops/ssot_audit.py`（索引/PLAN_STATUS の整合監査）
- `python3 scripts/ops/scripts_inventory.py --write`（`scripts/**` 棚卸しSSOTの再生成）
- `bash scripts/ops/save_patch.sh`（gitが不安定な場合のパッチ保存）
- `python3 scripts/ops/stage2_cutover_workspaces.py`（移設/互換symlink計画の一括適用。通常運用では触らない）

### Publish / OAuth（初回セットアップ）
- `python3 scripts/drive_oauth_setup.py`（Drive OAuth 初回セットアップ）
- `python3 scripts/drive_upload_oauth.py`（Drive upload token 作成/更新）
- `python3 scripts/youtube_publisher/oauth_setup.py`（YouTube OAuth 初回セットアップ）
- `scripts/youtube_publisher/README.md`（YouTube publish 手順）

### Planning/Script Sync（旧互換・慎重に）
- `python3 scripts/sync_all_scripts.py`（planning CSV ↔ status/assembled の同期）
- `python3 scripts/sync_ch02_scripts.py`（CH02限定の同期。原則 `sync_all_scripts.py` を優先）

### E2E（開発用）
- `bash scripts/e2e_smoke.sh`（軽量スモーク。CI用途が主）

---

## 4. 禁止（P2: 絶対に使わない / 削除候補）

※「現行SoTフロー外」かつ「誤誘導/品質事故の温床」になりやすいものを列挙。  
削除は `PLAN_LEGACY_AND_TRASH_CLASSIFICATION` の条件を満たしたものから順に実行する。

- 削除済み（復活禁止）:
  - 旧B-text QA / CH02 reading corrections / OpenRouter free-models helper / trend thumbnail PoC など
  - 危険/破綻している legacy helper（auto approve / mass overwrite / broken audit / shell wrapper）など
  - 証跡: `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`（Step 89–91）

---

## 5. 次の整理（実行タスク）

本書は運用ルールとして固定する（“TODO”を置かない）。

- 棚卸し更新: `python3 scripts/ops/scripts_inventory.py --write` → `ssot/ops/OPS_SCRIPTS_INVENTORY.md` を最新化
- 一時スクリプト: `scripts/_adhoc/`（P3。原則git管理しない。必要なら期限付きで明示的に add）
- 新規入口追加: P0/P1 を追加したら **必ず** `ssot/ops/OPS_ENTRYPOINTS_INDEX.md` / 本書 / Inventory を更新
- 削除: `PLAN_LEGACY_AND_TRASH_CLASSIFICATION` の条件を満たしたもののみ（archive-first → `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md` 記録）

# OPS_LOGGING_MAP — ログの正本配置/種類/増え方の完全マップ（現行→Target）

この文書は「どこに、どんなログが、どの処理で、どの粒度で溜まっていくか」を**コード実態に基づき確定**したSoT。  
ログ整理/リファクタリング/削除判断は本マップを正とする。

関連: `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`, `ssot/plans/PLAN_OPS_ARTIFACT_LIFECYCLE.md`, `ssot/plans/PLAN_REPO_DIRECTORY_REFACTOR.md`

---

## 0. ログの分類（Taxonomy）

### L1: 重要な構造化ログ（長期保持）
コスト/品質/監査/再現性に直結するため、**期限なし保持 or 月次アーカイブ**。

- **LLM使用量ログ**（JSONL）: `llm_usage.jsonl`
- **画像生成使用量ログ**（JSONL）: `image_usage.log`
- **TTS読み/品質プロファイルログ**（JSONL）: `tts_voicevox_reading.jsonl`
- **TTS LLMメタログ**（JSONL）: `tts_llm_usage.log`
- **監査集約ログ**（txt）: `audit_report_global.txt` など

### L3: 一時ログ/デバッグ/プロセス出力（短期保持）
再生成可能/一時的でサイズが増えるため、**日数ローテーション or keep-last-N**。

- run_dir / video_dir 内の工程ログ
- UIプロセスログ・PID
- スクリプト単発実行ログ
- 交換/修復/回帰テストログ

> 例外: L3でも運用上参照価値が高いものは L1 扱いに昇格（本マップで明示）。

---

注意:
- **正本のログルートは `packages/factory_common/paths.py:logs_root()`**（現行: `workspaces/logs/`）。  
  この文書中の `workspaces/logs/...` 記載は **`logs_root()/...` の意味**で読む（CWD差や将来移設で壊れないようにする）。
- “残す/消す/退避” の判断は本マップ（L1/L3）と `PLAN_OPS_ARTIFACT_LIFECYCLE.md` を正本にする。

## 1. ルート `workspaces/logs/`（現行のグローバルログ）

### 1.1 Cross‑cutting（全ドメイン共通）

- `workspaces/logs/llm_usage.jsonl`  
  - Writer:
    - `packages/factory_common/llm_client.py`（LLMClient。legacy スキーマ: `ts`, `task`, `provider`, `model`, `usage`）
    - `packages/factory_common/llm_router.py`（LLMRouter。router スキーマ: `status`, `task`, `provider`, `model`, `chain`, `latency_ms`, `usage?`, `error?`, `retry?`, `cache?`, `routing?`, `timestamp`）
    - `packages/factory_common/llm_api_failover.py`（API失敗→THINKフォールバック: `status=api_failover_*`, `task_id`, `pending?`, `runbook?`）
  - 形式: 1行JSON（複数スキーマ混在。将来的に schema_version で統一予定）
  - `routing`（任意）:
    - `LLM_AZURE_SPLIT_RATIO` が設定されている場合、Azure/非Azure の振り分け情報（policy/ratio/bucket/preferred_provider/routing_key）を出力する
  - Reader/UI: `apps/ui-backend/backend/routers/llm_usage.py`, `scripts/aggregate_llm_usage.py`
  - 種別: **L1**

- `workspaces/logs/agent_tasks/{pending,results,completed}/*.json`  
  - Writer: `packages/factory_common/agent_mode.py`, `scripts/agent_runner.py`, `packages/factory_common/llm_api_failover.py`
  - 役割: agent/think-mode の **キュー/結果キャッシュ**（enqueue → complete → rerun）
  - 関連:
    - `workspaces/logs/agent_tasks/coordination/memos/*.json`（申し送り/フォールバック通知）
      - Writer: `packages/factory_common/llm_api_failover.py`, `scripts/agent_org.py`
      - Reader: `python scripts/agent_org.py memos`, `python scripts/agent_org.py memo-show <MEMO_ID>`
    - `workspaces/logs/agent_tasks/coordination/locks/*.json`（任意: 作業スコープロック）
      - Writer/Reader: `scripts/agent_org.py`
      - Housekeeping: `python scripts/agent_org.py locks-prune` が期限切れ lock を `workspaces/logs/agent_tasks/coordination/locks/_archive/YYYYMM/` に退避する
    - `workspaces/logs/agent_tasks/coordination/board.json`（Shared Board: 共同メモ/状態の単一ファイル）
      - Writer/Reader: `python scripts/agent_org.py board {show,set,note,note-show,threads,thread-show,areas,area-set,template,normalize}`（noteは `note_id` を持つ）
    - `workspaces/logs/agent_tasks/coordination/events.jsonl`（協調イベントログ: append-only）
      - Writer/Reader: `scripts/agent_org.py`
    - `workspaces/logs/agent_tasks/coordination/agents/*.json`（agent registry: name/pid/heartbeat）
      - Writer/Reader: `scripts/agent_org.py`
    - `workspaces/logs/agent_tasks/coordination/assignments/*.json`（orchestrator→agent タスク割当）
      - Writer/Reader: `scripts/agent_org.py`
    - `workspaces/logs/agent_tasks/coordination/orchestrator/*`（orchestrator state/inbox/outbox）
      - Writer/Reader: `scripts/agent_org.py`
  - 備考: 不要になった結果は `ssot/plans/PLAN_OPS_ARTIFACT_LIFECYCLE.md` に従い退避/削除。
  - 種別: **L1（運用SoT）**

- `workspaces/logs/image_usage.log`  
  - Writer: `packages/factory_common/image_client.py`（Gemini ImageClient）
  - 形式: 1行JSON
    - `timestamp`, `success`, `task`, `tier`, `model`, `provider`, `request_id`, `duration_ms`, `prompt_sha256`, `attempt?`, `errors?`
  - Reader: `scripts/image_usage_report.py`
  - 種別: **L1**

- `workspaces/logs/image_rr_state.json`  
  - Writer: `packages/factory_common/image_client.py`（round‑robin state）
  - 種別: **状態ファイル（L3扱い）**

- `workspaces/logs/llm_api_cache/<task>/<cache_key>.json`  
  - Writer: `packages/factory_common/llm_api_cache.py`
  - 役割: LLM 呼び出しの **再利用キャッシュ**（同一入力の再実行を高速化/低コスト化）
  - 形式: JSON（レスポンス/メタデータ）
  - 種別: **L3（安全に削除可能。必要なら再生成される）**

### 1.2 Audio/TTS（グローバル）

- `workspaces/logs/tts_llm_usage.log`  
  - Writer: `packages/audio_tts/tts/llm_adapter.py`, `arbiter.py`, `auditor.py`
  - 形式: 1行JSON
    - `task`, `request_id?`, `model`, `provider`, `latency_ms`, `usage`, `error?`
  - Reader: `scripts/llm_logs_combined_report.py`
  - 種別: **L1**

- `workspaces/logs/tts_voicevox_reading.jsonl`  
  - Writer: `packages/audio_tts/tts/orchestrator.py`, `packages/audio_tts/tts/auditor.py`, `packages/audio_tts/scripts/run_contextual_reading_llm.py`
  - 形式: 1行JSON
    - 読み/ルビ補正のイベントログ（観測キー例）:
      - `timestamp`, `channel`, `video`, `block_id`, `token_index`
      - `surface`, `source`, `reason`
      - `after_kana`, `mecab_kana`, `voicevox_kana`, `ruby_kana`
      - `mora_range`, `suspicion_score?`, `voicevox_kana_norm?`
  - Reader: `scripts/aggregate_voicevox_reading_logs.py`
  - 種別: **L1**

- `workspaces/logs/annot_raw_fail.json`  
  - Writer: `packages/audio_tts/tts/llm_adapter.py`（annotate_tokens 失敗時の raw 出力保存）
  - 種別: **L3 / デバッグ**

- `workspaces/logs/annot_raw.json`
  - 現状: 観測されるが、コードからの生成参照は確認できない（手動/過去版の残骸の可能性）。
  - 種別: **L3（Legacy）**

### 1.3 Video/CapCut（グローバル）

- `workspaces/logs/llm_context_analyzer.log`  
  - Writer: `packages/video_pipeline/src/srt2images/llm_context_analyzer.py`
  - 形式: 1行JSON
    - `task`, `model`, `provider`, `latency_ms`, `usage`, `error?`
  - Reader: `scripts/llm_logs_combined_report.py`
  - 種別: **L3（必要なら L1 に昇格可）**

- `workspaces/logs/swap/swap_<timestamp>.log`  
  - Writer: `apps/ui-backend/backend/routers/swap.py`（UI Hub Swap API）
  - Reader: UI（Swap/CapCut 修復）/ `GET /api/swap/logs*`
  - 種別: **L3（30日ローテ）**

- `workspaces/logs/swap/history/<draft>/<index>/<timestamp>/*.png`  
  - Writer: `apps/ui-backend/backend/routers/swap.py`（swap 前の assets バックアップ。rollback 用）
  - Reader: UI（Swap の履歴表示/rollback）/ `GET /api/swap/images/history*`
  - 種別: **L3（短期保持。肥大しやすいのでローテ対象）**

- `workspaces/logs/swap/thumb_cache/<draft_key>/<max_dim>/*.png`  
  - Writer: `apps/ui-backend/backend/routers/swap.py`（画像プレビューのサムネキャッシュ）
  - Reader: UI（画像一覧プレビュー）
  - 種別: **L3（安全に削除可能。必要なら再生成される）**

### 1.4 UI / Ops（グローバル）

- `workspaces/logs/ui_hub/backend.log`, `backend.manual.log`, `frontend.log`, `frontend.manual.log`, `remotion_studio.log`, `start_all.nohup.log`  
  - Writer: `apps/ui-backend/tools/start_manager.py`, `scripts/start_all.sh`
  - 形式: stdout/stderr 合流ログ（起動ごとに上書き）
  - 種別: **L3 / keep-last‑N**

- `workspaces/logs/ui_hub/*.pid`  
  - Writer: `apps/ui-backend/tools/start_manager.py`, `scripts/start_all.sh`
  - 種別: **L3 / 状態ファイル**

- `workspaces/logs/ui_hub/video_production/<job_id>.log`  
  - Writer: `packages/video_pipeline/server/jobs.py`（FastAPI経由ジョブ）
  - 種別: **L3（job単位）**

- `workspaces/logs/ui/ui_tasks.db`  
  - Writer: `apps/ui-backend/backend/main.py`（BatchWorkflow のキュー/タスク状態）
  - 種別: **L1（UI運用SoTに近い）**

- `workspaces/logs/lock_metrics.db`  
  - Writer: `apps/ui-backend/backend/main.py`（ロック/並列制御のメトリクス蓄積）
  - Reader: `GET /api/admin/lock-metrics`
  - 種別: **L3（状態DB。肥大するならローテ/アーカイブ対象）**

- `workspaces/logs/ui/batch_workflow/<timestamp>_<CH>_<task_id>.log`  
  - Writer: `apps/ui-backend/backend/main.py`（BatchWorkflow実行ログ）
  - 種別: **L3**

- `workspaces/logs/ui/batch_tts_progress.json`, `workspaces/logs/ui/batch_tts_regeneration.log`  
  - Writer: `apps/ui-backend/backend/main.py`（BatchTTS start/progress/log API）, `scripts/batch_regenerate_tts.py`（バックグラウンド実行）
  - Reader: UI（BatchTtsProgressPanel）/ `GET /api/batch-tts/progress`, `GET /api/batch-tts/log`
  - 種別: **L3（短期保持。ローテ対象）**

- `workspaces/logs/regression/*`  
  - Writer例:
    - `apps/ui-backend/backend/main.py`（`channel_profile_edit_YYYYMMDD.log`）
    - `scripts/api_health_check.py`（`api_health_<timestamp>.log`）
    - `apps/ui-backend/backend/main.py`（`thumbnail_quick_history.jsonl` / `ssot_sync/*`）
    - `scripts/episode_ssot.py`（`archive_video_runs_dryrun_<CH>_<timestamp>.json`）
    - `scripts/ops/cleanup_video_runs.py`（`video_runs_cleanup_dryrun_<timestamp>.json`）
    - `scripts/ops/cleanup_broken_symlinks.py`（`broken_symlinks_<timestamp>.json` under `workspaces/logs/regression/broken_symlinks/`）
    - `scripts/ops/archive_capcut_local_drafts.py`（`capcut_local_drafts_archive_<timestamp>.json` under `workspaces/logs/regression/capcut_local_drafts_archive/`）
    - `scripts/ops/restore_video_runs.py`（`restore_video_runs_dryrun_<timestamp>.json` / `restore_report_<timestamp>.json`）
  - 種別:
    - `thumbnail_quick_history.jsonl` は **L1**（履歴価値あり）
    - それ以外は **L3**

- `workspaces/logs/ops/<operation>/<...>.log`  
  - Writer: 手動/単発の運用スクリプト（例: CapCutテンプレ正規化, 大量修復, 検証などの stdout リダイレクト）
  - 種別: **L3（短期保持。ローテ対象）**

### 1.5 Ad‑hoc scripts（単発/運用ログ）

主なWriterとファイル:
- `workspaces/logs/audit_global_execution.log`（観測される）:
  - 生成元: 監査/バッチ系の stdout リダイレクトの可能性が高い（コード参照は未確認）
  - 種別: **L1（監査ログとして保持）**
- `scripts/validate_status_sweep.py` → `workspaces/logs/regression/validate_status/validate_status_full_<ts>.json` + `workspaces/logs/validate_status_full_latest.json`（L3 / latest は上書き）
- `scripts/check_all_srt.sh` → `workspaces/logs/regression/srt_validation/srt_validation_<ts>.log` / `workspaces/logs/regression/srt_validation/srt_validation_failures_<ts>.txt` + `workspaces/logs/srt_validation_failures.txt`（latest, L3）
- `scripts/repair_manager.py` → `workspaces/logs/repair/{CH}-{NNN}.log`（L3）
- 手動TTS/リトライの出力（例: `workspaces/logs/tts_CH02_020.log`, `workspaces/logs/tts_retry*_CH02_019.log`, `workspaces/logs/tts_resume_*.log`）
  - 生成元: 端末リダイレクト（runnerが固定ファイルに書く設計ではない）
  - 種別: **L3（keep-last/30日）**
- 修復/品質確認の出力（例: `workspaces/logs/*repair*.log`, `workspaces/logs/check_quality_*.log`, `workspaces/logs/sequential_repair_*.log`, `workspaces/logs/batch_repair_*.log`）
  - 生成元: 端末リダイレクト/一時スクリプト
  - 種別: **L3（30日）**
- 集計/確認の出力（例: `workspaces/logs/llm_usage_summary.txt`）
  - 生成元: `scripts/aggregate_llm_usage.py` 等の stdout リダイレクト
  - 種別: **L3（30日）**
- 旧名/旧拡張子の残骸（例: `workspaces/logs/ch03_batch.out`）
  - 種別: **L3（Legacy）**
- 手動メモ → `workspaces/logs/pipeline_memo.txt`（L3。必要ならSSOTへ移す）
- その他 `scripts/*.py|*.sh` が `workspaces/logs/*.log|*.txt` を直接生成（Stage1で paths SSOT 化→Stage2で移設予定）

Legacy（削除済みのWriter。復活禁止）:
- scripts/audit_all.sh（削除済み: `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`）
- scripts/auto_approve.sh（削除済み: `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`）
- scripts/mass_regenerate_strict.sh（削除済み: `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`）
- scripts/run_ch03_batch.sh（削除済み: `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`）

---

### 1.6 現状スナップショット（2025-12-18 観測）

このセクションは cleanup 優先度のための “観測値”。値は日々変動するため、最新は `scripts/ops/logs_snapshot.py` を正とする。  
（Writer/Reader/L1-L3 の確定は上の各項目を正とする）

- file_count（logs_root 配下、全階層）: 1480
- top-level file counts:
  - llm_api_cache: 1153
  - agent_tasks: 202
  - regression: 62
  - ops: 28
  - (root): 21
  - swap: 6
  - ui_hub: 6
  - ui: 1
  - ssot: 1
- top-by-size（上位）:
  - `workspaces/logs/tts_voicevox_reading.jsonl`（約6.90MB）
  - `workspaces/logs/swap/history/.../0002.png`（約2.96MB）
  - `workspaces/logs/image_usage.log`（約2.57MB）
  - `workspaces/logs/llm_usage.jsonl`（約1.18MB）
  - `workspaces/logs/audit_report_global.txt`（約0.49MB）
  - `workspaces/logs/validate_status_full_latest.json`（約0.40MB）
  - `workspaces/logs/regression/validate_status/validate_status_full_20251213T105617Z.json`（約0.40MB）
  - `workspaces/logs/agent_tasks/coordination/events.jsonl`（約0.36MB）
  - `workspaces/logs/ui_hub/backend.log`（約0.12MB）
  - `workspaces/logs/pipeline_memo.txt`（約0.10MB）

再生成（スナップショット更新）:
- `python3 scripts/ops/logs_snapshot.py`

## 2. SoT配下（ドメイン/Run/Video単位のログ）

### 2.1 Script（台本）

- `workspaces/scripts/{CH}/{NNN}/logs/`（正本）
  - Writer: `packages/script_pipeline/runner.py`
  - 内容:
    - `{stage}_prompt.txt`
    - `{stage}_response.json`
  - 種別: **L3 / video単位**

- 規模スナップショット（2025-12-12）:
  - `workspaces/scripts/*/*/logs/` dir count: 91
  - `*/logs/*` file count: 1064

- `workspaces/scripts/_state/job_queue.jsonl`
  - Writer: `packages/script_pipeline/job_runner.py`
  - 種別: **L1（キューSoT）**

- `workspaces/scripts/_state/logs/{job_id}.log`
  - Writer: `packages/script_pipeline/job_runner.py`
  - 種別: **L3（14日ローテ。現行 cleanup_data.py が対象）**

### 2.2 Audio/TTS

- `workspaces/scripts/{CH}/{NNN}/audio_prep/log.json` 等
  - Writer: `packages/audio_tts/scripts/run_tts.py` → `tts/strict_orchestrator.py`
  - 種別: **L2/L3（中間。ready/published 後削除対象）**

- `workspaces/audio/final/{CH}/{NNN}/log.json`
  - Writer: `packages/audio_tts/scripts/run_tts.py`
  - 種別: **L0/L1（最終音声の証跡）**

- 規模スナップショット（2025-12-12）:
  - `workspaces/audio/final/*/*/` dir count: 319
  - `workspaces/audio/final/*/*/log.json` count: 189

- `workspaces/audio/final/{CH}/{NNN}/log_srt_only.json`, `b_text_build_log.json` 等
  - Writer: strict pipeline 内
  - 種別: **L1**

### 2.3 Video/CapCut（run単位）

- `workspaces/video/runs/{run_id}/logs/srt2images.log`（正本）
  - Writer: `packages/video_pipeline/src/srt2images/orchestration/utils.py::setup_logging`
  - 種別: **L3 / run単位**

- 規模スナップショット（2025-12-12）:
  - run dir count: 278
  - `workspaces/video/runs/*/logs/` dir count: 257
  - `workspaces/video/runs/*/logs/*` file count: 258

- `workspaces/video/runs/{run_id}/auto_run_info.json`（実行メタ）
  - Writer: `packages/video_pipeline/tools/auto_capcut_run.py`
  - 種別: **L1（run再現に必要）**

### 2.4 Package-local / Legacy（コード階層に残るログ）

- `packages/video_pipeline/logs/srt2images.log`
  - 現状: 観測されるが、正規フローでは run_dir の `workspaces/video/runs/{run_id}/logs/srt2images.log` が正本。
  - 種別: **L3（Legacy）**

- `packages/video_pipeline/logs/swap/swap_<timestamp>.log`
  - Writer: （旧）`legacy/video_pipeline/ui/gradio_app.py`（Legacy Swap UI。削除済み）
  - 現状: Swap のログ正本は `workspaces/logs/swap/swap_<timestamp>.log`（UI Hub: `/api/swap`）。
  - 種別: **L3（Legacy。存在したらTrash候補）**

- packages/video_pipeline/src/runtime/logs/notifications.jsonl
  - 現状: コード参照が確認できない（過去のコミット残骸の可能性）。legacy 側の同名ログも削除済み。
  - 方針: `PLAN_LEGACY_AND_TRASH_CLASSIFICATION.md` の基準で Trash 候補（再出現したら削除/ignore）。
  - 種別: **L3（Legacy/Trash候補）**

- `packages/audio_tts/logs/*.log`
  - 現状: 観測されるが、コード参照が確認できない（旧エージェント運用の残骸の可能性）。
  - 種別: **L3（Legacy）**

---

## 3. Stable（現行=Target: `workspaces/logs/`）

Stage2（cutover）は完了しており、ログの実体は **`workspaces/logs/` に集約済み**。  
ルート直下の互換symlink（`logs/` 等）は作らず、`logs_root()` は常に `workspaces/logs/` を指す。

```
workspaces/logs/
├─ llm_usage.jsonl              # L1: LLM usage (router/client/failover)
├─ image_usage.log              # L1: image usage
├─ tts_llm_usage.log            # L1: TTS LLM meta
├─ tts_voicevox_reading.jsonl   # L1: VOICEVOX reading events
├─ llm_context_analyzer.log     # L3: video context analyzer
├─ lock_metrics.db              # L3/L1: UI lock metrics DB
├─ ui_hub/                      # L3: backend/frontend/remotion logs + pid
├─ ui/                          # L1/L3: ui_tasks.db + batch_workflow logs
├─ regression/                  # L1/L3: quick_history + health/ssot_sync logs
├─ swap/                        # L3: swap logs + rollback history + thumb cache
├─ repair/                      # L3: repair logs
├─ ops/                         # L3: one-off ops logs
├─ llm_api_cache/               # L3: cache (safe to purge)
└─ agent_tasks/                 # L1: agent queue/coordination SoT
```

### 3.1 互換/境界
- `logs_root()` を使い、相対パス `workspaces/logs/...` の直書きは避ける（起動cwd差で壊れるため）。
- run_dir/logs は **run_dir 内に残す**（`workspaces/video/runs/{run_id}/logs/*`）。

---

## 4. ローテーション/保持（確定ルール）

### 4.1 L1（保持）
- `llm_usage.jsonl`, `image_usage.log`, `tts_llm_usage.log`, `tts_voicevox_reading.jsonl`, `audit_report_global.txt`, `thumbnail_quick_history.jsonl`
  - **無期限保持**。
  - サイズ肥大時は `workspaces/logs/_archive/YYYY‑MM/` へ月次zip（Stage6 cleanupで自動化）。

### 4.2 L3（短期）
- run/video/job単位ログ（`*/logs/*.log`）: **30日ローテ**
- `workspaces/logs/ui_hub/*`: **keep‑last‑10 起動分**（起動時に上書きなので、Stage6で世代保存に寄せる）
- `workspaces/scripts/_state/logs/*.log`（= `script_data_root()/_state/logs`）: **14日ローテ**
- `workspaces/logs/regression/*.log`: **30日ローテ**
- `workspaces/logs/swap/*.log`, `workspaces/logs/swap/history/**`, `workspaces/logs/swap/thumb_cache/**`: **30日ローテ**
- `workspaces/logs/repair/*.log`: **30日ローテ**
- `workspaces/logs/ops/**`: **30日ローテ**
- `workspaces/logs/llm_api_cache/**`: **必要なら 30日ローテ（キャッシュなので安全に削除可能）**

実行（手動/cron）:
- `python3 scripts/ops/cleanup_logs.py --run --keep-days 30`（workspaces/logs 直下の L3 を日数ローテ。report: `workspaces/logs/regression/logs_cleanup/`）
- `python3 scripts/ops/cleanup_logs.py --run --keep-days 30 --include-llm-api-cache`（llm_api_cache も含める。report: `workspaces/logs/regression/logs_cleanup/`）
- `python3 scripts/cleanup_data.py --run --keep-days 14`（workspaces/scripts の L3+一部L2。`audio_prep/` は final 音声が揃っている動画のみ対象）

---

## 5. 次の確定タスク（ログ整理のための追加調査）

- `scripts/` / `tools/` の ad‑hoc ログ生成箇所を **ファイル単位で Active/Legacy 判定**し、
  `workspaces/logs/ops/` へ寄せる（必要なら新しい OPS log を作る）。
- BatchTTS の progress/log は `workspaces/logs/ui/` に統一済み。ローテは `scripts/ops/cleanup_logs.py` の対象。
- 2025-12-12: `packages/video_pipeline/{src,ui/src}/memory/**` は参照ゼロの確実ゴミとして削除済み（`ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`）。

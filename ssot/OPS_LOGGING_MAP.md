# OPS_LOGGING_MAP — ログの正本配置/種類/増え方の完全マップ（現行→Target）

この文書は「どこに、どんなログが、どの処理で、どの粒度で溜まっていくか」を**コード実態に基づき確定**したSoT。  
ログ整理/リファクタリング/削除判断は本マップを正とする。

関連: `ssot/OPS_CONFIRMED_PIPELINE_FLOW.md`, `ssot/PLAN_OPS_ARTIFACT_LIFECYCLE.md`, `ssot/PLAN_REPO_DIRECTORY_REFACTOR.md`

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
- **正本のログルートは `factory_common/paths.py:logs_root()`**（現行: `workspaces/logs/` → `logs/` への symlink）。  
  この文書中の `logs/...` 記載は **`logs_root()/...` の意味**で読む（Stage2 で `workspaces/logs/` が実体化しても設計が壊れないようにする）。
- ルート `logs/` は gitignore 対象（`.gitignore: logs/`）のため、運用するとログが増えても git 差分に出にくい。
- “残す/消す/退避” の判断は本マップ（L1/L3）と `PLAN_OPS_ARTIFACT_LIFECYCLE.md` を正本にする。

## 1. ルート `logs/`（現行のグローバルログ）

### 1.1 Cross‑cutting（全ドメイン共通）

- `logs/llm_usage.jsonl`  
  - Writer:
    - `factory_common/llm_client.py`（LLMClient。legacy スキーマ: `ts`, `task`, `provider`, `model`, `usage`）
    - `factory_common/llm_router.py`（LLMRouter。router スキーマ: `status`, `task`, `provider`, `model`, `chain`, `latency_ms`, `usage?`, `error?`, `timestamp`）
    - `factory_common/llm_api_failover.py`（API失敗→THINKフォールバック: `status=api_failover_*`, `task_id`, `pending?`, `runbook?`）
  - 形式: 1行JSON（複数スキーマ混在。将来的に schema_version で統一予定）
  - Reader/UI: `ui/backend/routers/llm_usage.py`, `scripts/aggregate_llm_usage.py`
  - 種別: **L1**

- `logs/agent_tasks/{pending,results,completed}/*.json`  
  - Writer: `factory_common/agent_mode.py`, `scripts/agent_runner.py`, `factory_common/llm_api_failover.py`
  - 役割: agent/think-mode の **キュー/結果キャッシュ**（enqueue → complete → rerun）
  - 関連:
    - `logs/agent_tasks/coordination/memos/*.json`（申し送り/フォールバック通知）
      - Writer: `factory_common/llm_api_failover.py`, `scripts/agent_org.py`（旧: `scripts/agent_coord.py`）
      - Reader: `python scripts/agent_org.py memos`, `python scripts/agent_org.py memo-show <MEMO_ID>`
    - `logs/agent_tasks/coordination/locks/*.json`（任意: 作業スコープロック）
      - Writer/Reader: `scripts/agent_org.py`（旧: `scripts/agent_coord.py`）
    - `logs/agent_tasks/coordination/events.jsonl`（協調イベントログ: append-only）
      - Writer/Reader: `scripts/agent_org.py`
    - `logs/agent_tasks/coordination/agents/*.json`（agent registry: name/pid/heartbeat）
      - Writer/Reader: `scripts/agent_org.py`
    - `logs/agent_tasks/coordination/assignments/*.json`（orchestrator→agent タスク割当）
      - Writer/Reader: `scripts/agent_org.py`
    - `logs/agent_tasks/coordination/orchestrator/*`（orchestrator state/inbox/outbox）
      - Writer/Reader: `scripts/agent_org.py`
  - 備考: `logs/` は gitignore のため増えやすい。不要になった結果は `ssot/PLAN_OPS_ARTIFACT_LIFECYCLE.md` に従い退避/削除。
  - 種別: **L1（運用SoT）**

- `logs/image_usage.log`  
  - Writer: `factory_common/image_client.py`（Gemini ImageClient）
  - 形式: 1行JSON
    - `timestamp`, `success`, `task`, `tier`, `model`, `provider`, `request_id`, `duration_ms`, `prompt_sha256`, `attempt?`, `errors?`
  - Reader: `scripts/image_usage_report.py`
  - 種別: **L1**

- `logs/image_rr_state.json`  
  - Writer: `factory_common/image_client.py`（round‑robin state）
  - 種別: **状態ファイル（L3扱い）**

### 1.2 Audio/TTS（グローバル）

- `logs/tts_llm_usage.log`  
  - Writer: `audio_tts_v2/tts/llm_adapter.py`, `arbiter.py`, `auditor.py`
  - 形式: 1行JSON
    - `task`, `request_id?`, `model`, `provider`, `latency_ms`, `usage`, `error?`
  - Reader: `scripts/llm_logs_combined_report.py`
  - 種別: **L1**

- `logs/tts_voicevox_reading.jsonl`  
  - Writer: `audio_tts_v2/tts/orchestrator.py`, `audio_tts_v2/tts/auditor.py`, `audio_tts_v2/scripts/run_contextual_reading_llm.py`
  - 形式: 1行JSON
    - 読み/ルビ補正のイベントログ（観測キー例）:
      - `timestamp`, `channel`, `video`, `block_id`, `token_index`
      - `surface`, `source`, `reason`
      - `after_kana`, `mecab_kana`, `voicevox_kana`, `ruby_kana`
      - `mora_range`, `suspicion_score?`, `voicevox_kana_norm?`
  - Reader: `scripts/aggregate_voicevox_reading_logs.py`
  - 種別: **L1**

- `logs/annot_raw_fail.json`  
  - Writer: `audio_tts_v2/tts/llm_adapter.py`（annotate_tokens 失敗時の raw 出力保存）
  - 種別: **L3 / デバッグ**

- `logs/annot_raw.json`
  - 現状: 観測されるが、コードからの生成参照は確認できない（手動/過去版の残骸の可能性）。
  - 種別: **L3（Legacy）**

### 1.3 Video/CapCut（グローバル）

- `logs/llm_context_analyzer.log`  
  - Writer: `commentary_02_srt2images_timeline/src/srt2images/llm_context_analyzer.py`
  - 形式: 1行JSON
    - `task`, `model`, `provider`, `latency_ms`, `usage`, `error?`
  - Reader: `scripts/llm_logs_combined_report.py`
  - 種別: **L3（必要なら L1 に昇格可）**

- `logs/swap/swap_<timestamp>.log`  
  - Writer: `apps/ui-backend/backend/routers/swap.py`（UI Hub Swap API）
  - Reader: UI（Swap/CapCut 修復）/ `GET /api/swap/logs*`
  - 種別: **L3（30日ローテ）**

### 1.4 UI / Ops（グローバル）

- `logs/ui_hub/backend.log`, `backend.manual.log`, `frontend.log`, `frontend.manual.log`, `remotion_studio.log`, `start_all.nohup.log`  
  - Writer: `ui/tools/start_manager.py`, `scripts/start_all.sh`
  - 形式: stdout/stderr 合流ログ（起動ごとに上書き）
  - 種別: **L3 / keep-last‑N**

- `logs/ui_hub/*.pid`  
  - Writer: `ui/tools/start_manager.py`, `scripts/start_all.sh`
  - 種別: **L3 / 状態ファイル**

- `logs/ui_hub/video_production/<job_id>.log`  
  - Writer: `commentary_02_srt2images_timeline/ui/server/jobs.py`（FastAPI経由ジョブ）
  - 種別: **L3（job単位）**

- `logs/ui/ui_tasks.db`  
  - Writer: `ui/backend/main.py`（BatchWorkflow のキュー/タスク状態）
  - 種別: **L1（UI運用SoTに近い）**

- `logs/lock_metrics.db`  
  - Writer: `ui/backend/main.py`（ロック/並列制御のメトリクス蓄積）
  - Reader: `GET /api/admin/lock-metrics`
  - 種別: **L3（状態DB。肥大するならローテ/アーカイブ対象）**

- `logs/ui/batch_workflow/<timestamp>_<CH>_<task_id>.log`  
  - Writer: `ui/backend/main.py`（BatchWorkflow実行ログ）
  - 種別: **L3**

- `logs/regression/*`  
  - Writer例:
    - `ui/backend/main.py`（`channel_profile_edit_YYYYMMDD.log`）
    - `scripts/api_health_check.py`（`api_health_<timestamp>.log`）
    - `ui/backend/main.py`（`thumbnail_quick_history.jsonl` / `ssot_sync/*`）
  - 種別:
    - `thumbnail_quick_history.jsonl` は **L1**（履歴価値あり）
    - それ以外は **L3**

### 1.5 Ad‑hoc scripts（単発/運用ログ）

主なWriterとファイル:
- `scripts/audit_all.sh` → `logs/audit_report_global.txt`（L1）
- `logs/audit_global_execution.log`（観測される）:
  - 生成元: 監査/バッチ系の stdout リダイレクトの可能性が高い（コード参照は未確認）
  - 種別: **L1（監査ログとして保持）**
- `scripts/check_all_srt.sh` → `logs/srt_validation_<ts>.log` / `logs/srt_validation_failures.txt`（L3）
- `scripts/mass_regenerate_strict.sh` → `logs/mass_regenerate_<ts>.log`（L3）
- `scripts/repair_manager.py` → `logs/repair/{CH}-{NNN}.log`（L3）
- `scripts/run_ch03_batch.sh` → `logs/ch03_batch.log`（L3）
- `scripts/auto_approve.sh`（監視スクリプト）:
  - 参照: `logs/mass_generation.log`, `logs/fast_batch_repair.log`
  - 実行ログは `./scripts/auto_approve.sh > logs/auto_approve.log 2>&1` のようにリダイレクトされがち（L3）
- 手動TTS/リトライの出力（例: `logs/tts_CH02_020.log`, `logs/tts_retry*_CH02_019.log`, `logs/tts_resume_*.log`）
  - 生成元: 端末リダイレクト（runnerが固定ファイルに書く設計ではない）
  - 種別: **L3（keep-last/30日）**
- 修復/品質確認の出力（例: `logs/*repair*.log`, `logs/check_quality_*.log`, `logs/sequential_repair_*.log`, `logs/batch_repair_*.log`）
  - 生成元: 端末リダイレクト/一時スクリプト
  - 種別: **L3（30日）**
- 集計/確認の出力（例: `logs/llm_usage_summary.txt`）
  - 生成元: `scripts/aggregate_llm_usage.py` 等の stdout リダイレクト
  - 種別: **L3（30日）**
- 旧名/旧拡張子の残骸（例: `logs/ch03_batch.out`）
  - 種別: **L3（Legacy）**
- 手動メモ → `logs/pipeline_memo.txt`（L3。必要ならSSOTへ移す）
- その他 `scripts/*.py|*.sh` が `logs/*.log|*.txt` を直接生成（Stage1で paths SSOT 化→Stage2で移設予定）

---

### 1.6 現状スナップショット（2025-12-13 観測）

ルート `logs/` の “今” の状態（サイズ/件数）は、cleanup計画の優先度付けに使う。

- file_count: 90
- top-by-size（上位）:
  - `logs/ui_hub/frontend.log`（約11.64MB）
  - `logs/tts_voicevox_reading.jsonl`（約5.48MB）
  - `logs/image_usage.log`（約1.16MB）
  - `logs/ui_hub/start_all.nohup.log`（約1.05MB）
  - `logs/ui_hub/frontend.manual.log`（約0.55MB）
  - `logs/audit_report_global.txt`（約0.49MB）

#### 1.6.1 observed paths（2025-12-13）
再生成: `find logs -type f -maxdepth 4 -print | sort`
```
logs/agent_tasks/coordination/memos/memo__20251212T171830Z__5652e1e9.json
logs/agent_tasks/pending/script_outline__8e80cdc248e86c11d627743fbdbafa1a.json
logs/agent_tasks/pending/visual_section_plan__556282e7af8a39e130cb42a2b427509a.json
logs/annot_raw_fail.json
logs/annot_raw.json
logs/audit_global_execution.log
logs/audit_report_global.txt
logs/auto_approve.log
logs/batch_repair_20251207_v2.log
logs/batch_repair_20251207.log
logs/ch03_batch.out
logs/ch05_regen_no_llm_20251212_151631.log
logs/check_quality_029_fix.log
logs/check_quality_029.log
logs/debug_route2.log
logs/fast_batch_repair.log
logs/image_rr_state.json
logs/image_usage.log
logs/llm_context_analyzer.log
logs/llm_usage_summary.txt
logs/llm_usage.jsonl
logs/lock_metrics.db
logs/manual_ch06_002.log
logs/manual_ch06_003.log
logs/manual_ch06_004.log
logs/mass_generation.log
logs/pipeline_memo.txt
logs/regression/channel_profile_edit_20251128.log
logs/repair/CH02-001.log
logs/repair/CH02-002.log
logs/repair/CH02-003.log
logs/repair/CH02-004.log
logs/repair/CH02-005.log
logs/repair/CH02-006.log
logs/repair/CH06-005.log
logs/repair/CH06-006.log
logs/repair/CH06-007.log
logs/repair/CH06-008.log
logs/repair/CH06-009.log
logs/repair/CH06-010.log
logs/repair/CH06-011.log
logs/repair/CH06-012.log
logs/repair/CH06-013.log
logs/repair/CH06-014.log
logs/repair/CH06-015.log
logs/repair/CH06-016.log
logs/repair/CH06-017.log
logs/repair/CH06-018.log
logs/repair/CH06-019.log
logs/repair/CH06-020.log
logs/repair/CH06-021.log
logs/repair/CH06-022.log
logs/repair/CH06-023.log
logs/repair/CH06-024.log
logs/repair/CH06-025.log
logs/repair/CH06-026.log
logs/repair/CH06-027.log
logs/repair/CH06-028.log
logs/repair/CH06-029.log
logs/repair/CH06-030.log
logs/repair/CH06-031.log
logs/repair/CH06-032.log
logs/repair/CH06-033.log
logs/sequential_repair_20251207.log
logs/srt_validation_failures.txt
logs/swap/swap_20251202_062745.log
logs/swap/swap_20251202_062756.log
logs/swap/swap_20251202_062830.log
logs/swap/swap_20251202_062858.log
logs/swap/swap_20251202_063549.log
logs/swap/swap_20251202_063916.log
logs/test_ch02_001_excerpt.json
logs/tts_CH02_020.log
logs/tts_llm_usage.log
logs/tts_resume_CH02_019.log
logs/tts_retry_CH02_019.log
logs/tts_retry2_CH02_019.log
logs/tts_voicevox_reading.jsonl
logs/ui_hub/backend.log
logs/ui_hub/backend.manual.log
logs/ui_hub/backend.pid
logs/ui_hub/frontend.log
logs/ui_hub/frontend.manual.log
logs/ui_hub/frontend.pid
logs/ui_hub/remotion_studio.log
logs/ui_hub/remotion_studio.pid
logs/ui_hub/start_all.nohup.log
logs/ui/ui_tasks.db
```

## 2. SoT配下（ドメイン/Run/Video単位のログ）

### 2.1 Script（台本）

- `script_pipeline/data/{CH}/{NNN}/logs/`
  - Writer: `script_pipeline/runner.py`
  - 内容:
    - `{stage}_prompt.txt`
    - `{stage}_response.json`
  - 種別: **L3 / video単位**

- 規模スナップショット（2025-12-12）:
  - `logs/` dir count: 91
  - `*/logs/*` file count: 1064

- `script_pipeline/data/_state/job_queue.jsonl`
  - Writer: `script_pipeline/job_runner.py`
  - 種別: **L1（キューSoT）**

- `script_pipeline/data/_state/logs/{job_id}.log`
  - Writer: `script_pipeline/job_runner.py`
  - 種別: **L3（14日ローテ。現行 cleanup_data.py が対象）**

### 2.2 Audio/TTS

- `script_pipeline/data/{CH}/{NNN}/audio_prep/log.json` 等
  - Writer: `audio_tts_v2/scripts/run_tts.py` → `tts/strict_orchestrator.py`
  - 種別: **L2/L3（中間。ready/published 後削除対象）**

- `audio_tts_v2/artifacts/final/{CH}/{NNN}/log.json`
  - Writer: `audio_tts_v2/scripts/run_tts.py`
  - 種別: **L0/L1（最終音声の証跡）**

- 規模スナップショット（2025-12-12）:
  - `audio_tts_v2/artifacts/final/*/*/` dir count: 319
  - `audio_tts_v2/artifacts/final/*/*/log.json` count: 189

- `audio_tts_v2/artifacts/final/{CH}/{NNN}/log_srt_only.json`, `b_text_build_log.json` 等
  - Writer: strict pipeline 内
  - 種別: **L1**

### 2.3 Video/CapCut（run単位）

- `commentary_02_srt2images_timeline/output/{run_id}/logs/srt2images.log`
  - Writer: `commentary_02_srt2images_timeline/src/srt2images/orchestration/utils.py::setup_logging`
  - 種別: **L3 / run単位**

- 規模スナップショット（2025-12-12）:
  - run dir count: 278
  - `output/*/logs/` dir count: 257
  - `output/*/logs/*` file count: 258

- `output/{run_id}/auto_run_info.json`（実行メタ）
  - Writer: `tools/auto_capcut_run.py`
  - 種別: **L1（run再現に必要）**

### 2.4 Package-local / Legacy（コード階層に残るログ）

- `commentary_02_srt2images_timeline/logs/srt2images.log`
  - 現状: 観測されるが、正規フローでは run_dir の `output/{run_id}/logs/srt2images.log` が正本。
  - 種別: **L3（Legacy）**

- `commentary_02_srt2images_timeline/logs/swap/swap_<timestamp>.log`
  - Writer: `commentary_02_srt2images_timeline/ui/gradio_app.py`（Legacy Swap UI）
  - 現状: UI Hub（`/api/swap`）のログ正本は `logs/swap/swap_<timestamp>.log`。
  - 種別: **L3（Legacy）**

- `commentary_02_srt2images_timeline/src/runtime/logs/notifications.jsonl`, `commentary_02_srt2images_timeline/ui/src/runtime/logs/notifications.jsonl`
  - 現状: コード参照が確認できない（ログのコミット残骸の可能性）。
  - 方針: `PLAN_LEGACY_AND_TRASH_CLASSIFICATION.md` の基準で Trash 候補（削除/ignore を計画化）。
  - 種別: **L3（Legacy/Trash候補）**

- `audio_tts_v2/logs/*.log`
  - 現状: 観測されるが、コード参照が確認できない（旧エージェント運用の残骸の可能性）。
  - 種別: **L3（Legacy）**

---

## 3. Target（リファクタ後のログ正本配置）

`PLAN_REPO_DIRECTORY_REFACTOR.md` の Stage2/5 完了後、ログは以下に収束させる。

```
workspaces/logs/
├─ pipeline/                    # Cross-cutting usage / domain global
│  ├─ llm_usage.jsonl
│  ├─ image_usage.log
│  ├─ tts_llm_usage.log
│  ├─ tts_voicevox_reading.jsonl
│  └─ llm_context_analyzer.log
├─ ui/
│  ├─ hub/                      # backend/frontend/remotion preview logs + pid
│  ├─ batch_workflow/           # UI batch logs + configs + queue state
│  └─ regression/               # quick_history / ssot_sync / health logs
├─ jobs/
│  ├─ script_pipeline/          # job_runner logs
│  └─ video_production/         # CapCut/Swap jobs
└─ _archive/                    # month/day zip
```

### 3.1 既存→Target マッピング
- `logs/llm_usage.jsonl` → `workspaces/logs/pipeline/llm_usage.jsonl`
- `logs/image_usage.log` → `workspaces/logs/pipeline/image_usage.log`
- `logs/tts_llm_usage.log` → `workspaces/logs/pipeline/tts_llm_usage.log`
- `logs/tts_voicevox_reading.jsonl` → `workspaces/logs/pipeline/tts_voicevox_reading.jsonl`
- `logs/llm_context_analyzer.log` → `workspaces/logs/pipeline/llm_context_analyzer.log`
- `logs/ui_hub/*` → `workspaces/logs/ui/hub/*`
- `logs/ui/*` → `workspaces/logs/ui/batch_workflow/*`
- `logs/regression/*` → `workspaces/logs/ui/regression/*`
- `script_pipeline/data/_state/logs/*` → `workspaces/logs/jobs/script_pipeline/*`
- `logs/ui_hub/video_production/*` → `workspaces/logs/jobs/video_production/*`
- run_dir `output/{run_id}/logs/*` は **run_dir内に残す**（`workspaces/video/runs/{run_id}/logs/*` へ自然移動）

Stage1（paths SSOT）で上記 root を getter 化し、Stage2で物理移設＋symlink互換。

---

## 4. ローテーション/保持（確定ルール）

### 4.1 L1（保持）
- `llm_usage.jsonl`, `image_usage.log`, `tts_llm_usage.log`, `tts_voicevox_reading.jsonl`, `audit_report_global.txt`, `thumbnail_quick_history.jsonl`
  - **無期限保持**。
  - サイズ肥大時は `workspaces/logs/_archive/YYYY‑MM/` へ月次zip（Stage6 cleanupで自動化）。

### 4.2 L3（短期）
- run/video/job単位ログ（`*/logs/*.log`）: **30日ローテ**
- `logs/ui_hub/*`: **keep‑last‑10 起動分**（起動時に上書きなので、Stage6で世代保存に寄せる）
- `script_pipeline/data/_state/logs/*.log`: **14日ローテ（現行維持）**
- `logs/regression/*.log`: **30日ローテ**
- `logs/swap/*.log`, `logs/repair/*.log`: **30日ローテ**

実行（手動/cron）:
- `python scripts/ops/cleanup_logs.py --run --keep-days 30`（logs 直下の L3 を日数ローテ）
- `python scripts/cleanup_data.py --run --keep-days 14`（script_pipeline/data の L3+一部L2）

---

## 5. 次の確定タスク（ログ整理のための追加調査）

- `scripts/` / `tools/` の ad‑hoc ログ生成箇所を **ファイル単位で Active/Legacy 判定**し、
  Stage3後に `workspaces/logs/pipeline/ops/` へ寄せる（必要なら新しい OPS log を作る）。
- `batch_tts_regeneration.log`（repo直下）を paths SSOT 経由で `logs/ui/batch_workflow/` に統一（Stage1対象）。
- 2025-12-12: `commentary_02_srt2images_timeline/{src,ui/src}/memory/**` は参照ゼロの確実ゴミとして削除済み（`ssot/OPS_CLEANUP_EXECUTION_LOG.md`）。

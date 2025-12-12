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

## 1. ルート `logs/`（現行のグローバルログ）

### 1.1 Cross‑cutting（全ドメイン共通）

- `logs/llm_usage.jsonl`  
  - Writer: `factory_common/llm_client.py`（LLMClient の全呼び出し）
  - 形式: 1行JSON
    - `ts`, `task`, `provider`, `model`, `usage`
  - Reader/UI: `ui/backend/routers/llm_usage.py`, `scripts/aggregate_llm_usage.py`
  - 種別: **L1**

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
  - Writer: `audio_tts_v2/tts/orchestrator.py`（strict 完走時に追記）
  - 形式: 1行JSON
    - `timestamp`, `channel`, `video`, `layer_times{ruby,risk,arbiter,synth,total}`,  
      `tts_reading_calls`, `audit_blocks_marked`, `risky_terms`, `ruby_patches`
  - Reader: `scripts/aggregate_voicevox_reading_logs.py`
  - 種別: **L1**

- `logs/annot_raw_fail.json`  
  - Writer: `audio_tts_v2/tts/llm_adapter.py`（annotate_tokens 失敗時の raw 出力保存）
  - 種別: **L3 / デバッグ**

### 1.3 Video/CapCut（グローバル）

- `logs/llm_context_analyzer.log`  
  - Writer: `commentary_02_srt2images_timeline/src/srt2images/llm_context_analyzer.py`
  - 形式: 1行JSON
    - `task`, `model`, `provider`, `latency_ms`, `usage`, `error?`
  - Reader: `scripts/llm_logs_combined_report.py`
  - 種別: **L3（必要なら L1 に昇格可）**

- `logs/swap/swap_<timestamp>.log`  
  - Writer: `commentary_02_srt2images_timeline/ui/gradio_app.py`（Swap UI）
  - Reader: CapCut Swap UI / `ui/backend/routers/swap.py`
  - 種別: **L3（30日ローテ）**

### 1.4 UI / Ops（グローバル）

- `logs/ui_hub/backend.log`, `frontend.log`, `remotion_studio.log`, `start_all.nohup.log`  
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
- `scripts/check_all_srt.sh` → `logs/srt_validation_<ts>.log` / `logs/srt_validation_failures.txt`（L3）
- `scripts/mass_regenerate_strict.sh` → `logs/mass_regenerate_<ts>.log`（L3）
- `scripts/repair_manager.py` → `logs/repair/{CH}-{NNN}.log`（L3）
- `scripts/run_ch03_batch.sh` → `logs/ch03_batch.log`（L3）
- その他 `scripts/*.py|*.sh` が `logs/*.log|*.txt` を直接生成（Stage1で paths SSOT 化→Stage2で移設予定）

---

## 2. SoT配下（ドメイン/Run/Video単位のログ）

### 2.1 Script（台本）

- `script_pipeline/data/{CH}/{NNN}/logs/`
  - Writer: `script_pipeline/runner.py`
  - 内容:
    - `{stage}_prompt.txt`
    - `{stage}_response.json`
  - 種別: **L3 / video単位**

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

- `audio_tts_v2/artifacts/final/{CH}/{NNN}/log_srt_only.json`, `b_text_build_log.json` 等
  - Writer: strict pipeline 内
  - 種別: **L1**

### 2.3 Video/CapCut（run単位）

- `commentary_02_srt2images_timeline/output/{run_id}/logs/srt2images.log`
  - Writer: `commentary_02_srt2images_timeline/src/srt2images/orchestration/utils.py::setup_logging`
  - 種別: **L3 / run単位**

- `output/{run_id}/auto_run_info.json`（実行メタ）
  - Writer: `tools/auto_capcut_run.py`
  - 種別: **L1（run再現に必要）**

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

---

## 5. 次の確定タスク（ログ整理のための追加調査）

- `scripts/` / `tools/` の ad‑hoc ログ生成箇所を **ファイル単位で Active/Legacy 判定**し、
  Stage3後に `workspaces/logs/pipeline/ops/` へ寄せる（必要なら新しい OPS log を作る）。
- `batch_tts_regeneration.log`（repo直下）を paths SSOT 経由で `logs/ui/batch_workflow/` に統一（Stage1対象）。
- `commentary_02/ui/src/memory/operations/operation_log.jsonl` 等の残骸は参照ゼロのため Trash候補（管理者確認待ち）。


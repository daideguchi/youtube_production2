# Agent Runbooks (SSOT)

このフォルダは **agent/think-mode 運用の指示書（Runbook）の正本** です。  
`LLM_EXEC_SLOT=3（THINK）` / `LLM_EXEC_SLOT=4（AGENT）` でパイプラインを動かすと、`workspaces/logs/agent_tasks/pending/` に「処理待ちタスク」が生成されます。
また、API LLM が失敗した場合も **API→THINK failover（デフォルト有効）** により pending が生成されます（無効化は `LLM_EXEC_SLOT=5`）。

## 原則
- Runbook は「そのまま実行できる」レベルで書く（コマンド/分岐/成果物/チェックを具体化）。
- 変更したら必ず Runbook 内の `最終更新日` と `ADR` を更新する。
- タスク→Runbook の割当は `configs/agent_runbooks.yaml` が正本。
- 複数エージェント運用では **作業前に claim して担当者名を残す**（`LLM_AGENT_NAME` → `claimed_by` / `completed_by`）。

## 使い方（基本）
1. THINK MODE で実行（結果が無ければ止まる）
   - 推奨: `./scripts/think.sh --all-text -- <command> [args...]`
2. pending を確認
   - `python scripts/agent_runner.py list`
   - フォールバック/申し送りのメモ確認（任意）: `python scripts/agent_org.py memos`
   - 複数エージェント組織運用（任意）: [`RUNBOOK_AGENT_ORG_COORDINATION.md`](/ssot/agent_runbooks/RUNBOOK_AGENT_ORG_COORDINATION.md)
   - 先に担当を明示（推奨）:
     - `export LLM_AGENT_NAME=Mike`
     - `python scripts/agent_runner.py claim <TASK_ID>`
3. タスク詳細を見る
   - `python scripts/agent_runner.py show <TASK_ID>`
   - コピペ用（端末操作できるエージェント向け）: `python scripts/agent_runner.py prompt <TASK_ID>`
   - 外部チャット用（端末操作不可）: `python scripts/agent_runner.py chat <TASK_ID>`
   - まとめファイル: `python scripts/agent_runner.py bundle <TASK_ID> --include-runbook`
4. 該当 Runbook を開き、手順どおりに推論/作業して **results を作る**
   - `python scripts/agent_runner.py complete <TASK_ID> --content-file /path/to/output.txt`
5. 同じコマンドを再実行（results が返されて先に進む）

## ファイル
- [`OVERVIEW_PINNED.md`](/ssot/agent_runbooks/OVERVIEW_PINNED.md): 重要事項のまとめ（迷ったらここ）
- [`RUNBOOK_TEMPLATE.md`](/ssot/agent_runbooks/RUNBOOK_TEMPLATE.md): 新規Runbookのテンプレ
- [`RUNBOOK_GENERIC_LLM_TASK.md`](/ssot/agent_runbooks/RUNBOOK_GENERIC_LLM_TASK.md): タスク共通の最小Runbook
- [`RUNBOOK_SCRIPT_WRITING.md`](/ssot/agent_runbooks/RUNBOOK_SCRIPT_WRITING.md): 台本生成（script_*）
- [`RUNBOOK_AUDIO_TTS.md`](/ssot/agent_runbooks/RUNBOOK_AUDIO_TTS.md): 音声/TTS（tts_*）
- [`RUNBOOK_JOB_SCRIPT_PIPELINE.md`](/ssot/agent_runbooks/RUNBOOK_JOB_SCRIPT_PIPELINE.md): 台本生成（end-to-end）
- [`RUNBOOK_JOB_AUDIO_TTS_PIPELINE.md`](/ssot/agent_runbooks/RUNBOOK_JOB_AUDIO_TTS_PIPELINE.md): 音声生成（end-to-end）
- [`RUNBOOK_JOB_RUNNER_DAEMON.md`](/ssot/agent_runbooks/RUNBOOK_JOB_RUNNER_DAEMON.md): `script_pipeline.job_runner` 常駐（launchd/cron）
- [`assets/job_runner.service`](/ssot/agent_runbooks/assets/job_runner.service): daemon/service 定義（必要時のみ）
- [`RUNBOOK_VISUAL_CUES_PLAN.md`](/ssot/agent_runbooks/RUNBOOK_VISUAL_CUES_PLAN.md): srt2images の cues 計画（visual_image_cues_plan）
- [`RUNBOOK_THINK_MODE_OPERATOR.md`](/ssot/agent_runbooks/RUNBOOK_THINK_MODE_OPERATOR.md): THINK MODEのループ運用
- [`RUNBOOK_AGENT_ORG_COORDINATION.md`](/ssot/agent_runbooks/RUNBOOK_AGENT_ORG_COORDINATION.md): Orchestrator + Workers の協調運用
- [`RUNBOOK_CLEANUP_DATA.md`](/ssot/agent_runbooks/RUNBOOK_CLEANUP_DATA.md): `workspaces/scripts` の中間物/ログ整理

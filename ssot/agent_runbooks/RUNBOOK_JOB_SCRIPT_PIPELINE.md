# RUNBOOK_JOB_SCRIPT_PIPELINE — 台本生成（end-to-end / API固定）

## Runbook metadata
- **Runbook ID**: RUNBOOK_JOB_SCRIPT_PIPELINE
- **ステータス**: Active
- **対象**: `scripts/ops/script_runbook.py` で台本を `script_validation` まで収束させる（API実行）
- **最終更新日**: 2026-01-10

## 0. 固定ルール（最重要）
- `script_*`（台本）は **LLM API（Fireworks）固定**。
- `THINK/AGENT`（`LLM_EXEC_SLOT=3|4`）や Codex/agent 代行で台本を書かせない（実装で遮断済み）。

## 1. 目的（DoD）
- 入口固定（runbook）で `new/resume` を回し、`script_validation` まで完了させる。

## 2. 実行（入口固定: exec-slot=0 明示）
新規作成:
```bash
./scripts/with_ytm_env.sh --exec-slot 0 \
  python3 scripts/ops/script_runbook.py new --channel CH06 --video 033 --until script_validation --max-iter 6
```

途中再開（止まった回の復帰）:
```bash
./scripts/with_ytm_env.sh --exec-slot 0 \
  python3 scripts/ops/script_runbook.py resume --channel CH06 --video 033 --until script_validation --max-iter 6
```

## 3. 止まったとき
### 3.1 台本系の停止（`script_*`）
- 入口/Planning/プロンプトを直して **同じコマンドで再実行**（resume）。
- API不調/キー未設定などで失敗した場合は、まず環境（`FIREWORKS_SCRIPT` 等）を復旧してから rerun。

### 3.2 pending が出たとき（非`script_*`のみ）
Web検索など非台本タスクが pending 化した場合:
- `python scripts/agent_runner.py list`
- `python scripts/agent_runner.py bundle <TASK_ID> --include-runbook`
- runbook の指示どおりに results を投入:
  - `python scripts/agent_runner.py complete <TASK_ID> --content-file /path/to/content.txt`
- 元コマンドを rerun

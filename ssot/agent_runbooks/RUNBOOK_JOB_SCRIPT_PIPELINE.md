# RUNBOOK_JOB_SCRIPT_PIPELINE — 台本生成（end-to-end）

## Runbook metadata
- **Runbook ID**: RUNBOOK_JOB_SCRIPT_PIPELINE
- **ステータス**: Active
- **対象**: script_pipeline を THINK MODE で完走させる
- **最終更新日**: 2025-12-12

## 1. 目的（DoD）
- `python -m script_pipeline.cli ...` を THINK MODE で完走。

## 2. 実行
```bash
./scripts/think.sh --script -- \
  python -m script_pipeline.cli run-all --channel CH06 --video 033
```

## 3. pending が出たら
- `python scripts/agent_runner.py list`
- `python scripts/agent_runner.py bundle <TASK_ID> --include-runbook`
- bundle の指示どおりに出力を作って:
  - `python scripts/agent_runner.py complete <TASK_ID> --content-file /path/to/content.txt`
- 元コマンドを rerun


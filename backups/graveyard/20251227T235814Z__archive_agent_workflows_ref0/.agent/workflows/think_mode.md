---
description: THINK MODE: run a pipeline command, resolve pending agent_tasks, and rerun until completion.
---

# THINK MODE（エージェントが思考して完走させる）

## 目的
API LLM 呼び出しの代わりに、エージェントが推論して results を投入し、パイプラインを完走させる。

## 手順（エージェント用）
1. 対象コマンドを THINK MODE で実行:
   - `./scripts/think.sh --all-text <command> [args...]`
2. pending が出たら:
   - `python scripts/agent_runner.py list`
   - `python scripts/agent_runner.py bundle <TASK_ID> --include-runbook`
   - バンドルの指示どおりに「要求された出力だけ」を作り:
     - `python scripts/agent_runner.py complete <TASK_ID> --content-file /path/to/content.txt`
3. 1 のコマンドを同じ引数で再実行し、pending が無くなるまで繰り返す。

参照Runbook: `ssot/agent_runbooks/RUNBOOK_THINK_MODE_OPERATOR.md`


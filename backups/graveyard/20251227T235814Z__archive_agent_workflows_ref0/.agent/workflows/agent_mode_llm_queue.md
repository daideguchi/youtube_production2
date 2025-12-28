---
description: Replace API LLM calls with an agent queue (LLM_MODE=agent) and resolve pending tasks via scripts/agent_runner.py
---

# Agent Mode — LLM Queue (enqueue → complete → rerun)

## 目的
API LLM 呼び出しを止めて、AIエージェント（あなた）が推論して results を投入できるようにする。

## 最短（一発）
```bash
./scripts/think.sh --all-text -- \
  python -m script_pipeline.cli run-all --channel CH06 --video 033
```

## 使い方（手動）

### 1) agent-mode を有効化
```bash
export LLM_MODE=agent
export LLM_AGENT_TASK_PREFIXES=script_,tts_
```

### 2) いつも通り実行（途中で止まる）
パイプラインを実行すると、LLM が必要な地点で停止し、`logs/agent_tasks/pending/` にタスクが作られる。

### 3) pending を確認
```bash
python scripts/agent_runner.py list
python scripts/agent_runner.py show <TASK_ID>
```

### 4) エージェントが内容を作る
- `pending.runbook_path` の Runbook を読んで出力を作る
- JSON 指定（`response_format=json_object`）のときは **JSONのみ**（前後に文章を混ぜない）

```bash
python scripts/agent_runner.py bundle <TASK_ID> --include-runbook
python scripts/agent_runner.py complete <TASK_ID> --content-file /path/to/content.txt
```

### 5) results を投入して再実行
止まった元コマンドをもう一度実行して続きへ。


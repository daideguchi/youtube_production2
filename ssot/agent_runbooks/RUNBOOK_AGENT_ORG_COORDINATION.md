# RUNBOOK_AGENT_ORG_COORDINATION — AIエージェント組織（Orchestrator + Workers）運用

## Runbook metadata
- **Runbook ID**: RUNBOOK_AGENT_ORG_COORDINATION
- **ステータス**: Active
- **対象**: 複数エージェント並列運用（作業衝突防止 / 役割割当 / 申し送り）
- **最終更新日**: 2025-12-12

## 1. 目的（DoD）
- Orchestrator を 1つに固定し、役割・担当・申し送りを運用できる状態にする。
- 「誰が何をしてるか」「ここは触るな」がファイルSoTとして残る。

## 2. 前提
- 正本ディレクトリは `logs/agent_tasks/coordination/`（`LLM_AGENT_QUEUE_DIR` で変更可）。
- 重要: zsh は `ui/**` のような `**` を展開するため、glob は **必ずクォート**する（`'ui/**'`）。

## 3. Orchestrator（任命・奪取不可）
### 3.1 起動
```bash
python scripts/agent_org.py orchestrator start --name dd-orch
python scripts/agent_org.py orchestrator status
```

### 3.2 停止（交代）
```bash
python scripts/agent_org.py orchestrator stop
```

※ Orchestrator は `flock` で単一リースを保持する。2人目は起動できない。

## 4. Worker Agent（名前・心拍）
### 4.1 heartbeat を起動（各Agentごと）
```bash
python scripts/agent_org.py agents start --name Mike --role worker
python scripts/agent_org.py agents start --name Eric --role worker
python scripts/agent_org.py agents list
```

### 4.2 停止（不要なら）
```bash
python scripts/agent_org.py agents stop --name Mike
```

## 5. 役割の付与（Orchestrator request）
```bash
python scripts/agent_org.py orchestrator request \\
  --action set_role \\
  --payload-json '{\"agent_name\":\"Mike\",\"role\":\"audio_worker\"}' \\
  --wait-sec 3
```

確認:
```bash
python scripts/agent_org.py agents list
```

## 6. pending タスクの割当（assignment + memo）
### 6.1 pending を確認
```bash
python scripts/agent_runner.py list
```

### 6.2 Orchestrator が割当
```bash
python scripts/agent_org.py orchestrator request \\
  --action assign_task \\
  --payload-json '{\"task_id\":\"<TASK_ID>\",\"agent_name\":\"Mike\",\"note\":\"handle this\"}' \\
  --wait-sec 3
```

### 6.3 Worker は memo を見る
```bash
python scripts/agent_org.py memos --to Mike
```

## 7. 作業スコープロック（soft access control）
### 7.1 lock を置く（触る前に）
```bash
python scripts/agent_org.py lock 'ui/**' --mode no_touch --ttl-min 60 --note 'dd working'
python scripts/agent_org.py locks --path ui/backend/main.py
```

### 7.2 lock を外す
```bash
python scripts/agent_org.py unlock <LOCK_ID>
```

## 8. 申し送り（memo）
```bash
python scripts/agent_org.py memo --to Mike --subject 'Heads up' --body 'ui/backend は触らないで'
python scripts/agent_org.py memos --to Mike
```

## 9. 監査（events）
```bash
tail -n 50 logs/agent_tasks/coordination/events.jsonl
```


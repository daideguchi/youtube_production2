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
- 正本ディレクトリは `workspaces/logs/agent_tasks/coordination/`（`LLM_AGENT_QUEUE_DIR` で変更可）。
- 重要: zsh は `apps/ui-frontend/**` のような `**` を展開するため、glob は **必ずクォート**する（`'apps/ui-frontend/**'`）。

## 2.1 UI（可視化）
- UIページ: `/agent-org`
- Backend API: `/api/agent-org/*`
- もし `/api/agent-org/*` が 404 なら、backend の再起動が必要（新規 router を reload が拾わないことがある）。

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
重要:
- 並列運用では **各Codex/ターミナルごと**に agent name が必須（`lock/memo/board` 等の write 操作の attribution を壊さないため）。
- `export LLM_AGENT_NAME=...` を毎回やりたくない場合、`scripts/agent_org.py` の write系は **初回だけプロンプトで名前入力→記憶**される（以後は自動）。
- 推奨命名: `<owner>-<area>-<nn>`（例: `dd-ui-01`）

### 4.1 heartbeat を起動（各Agentごと）
```bash
export LLM_AGENT_NAME=Mike
python scripts/agent_org.py agents start --name "$LLM_AGENT_NAME" --role worker
# 別ターミナルでもう1体:
export LLM_AGENT_NAME=Eric
python scripts/agent_org.py agents start --name "$LLM_AGENT_NAME" --role worker
python scripts/agent_org.py agents list
```
（推奨: heartbeat + board を同時に更新）:
```bash
python3 scripts/ops/agent_bootstrap.py --name "$LLM_AGENT_NAME" --role worker --doing "ui: ..." --next "..." --tags ui
```

### 4.2 停止（不要なら）
```bash
python scripts/agent_org.py agents stop --name Mike
```

## 5. 役割の付与（Orchestrator request）
```bash
python scripts/agent_org.py agents list
python scripts/agent_org.py orchestrator request \\
  --action set_role \\
  --payload-json '{\"agent_id\":\"<AGENT_ID>\",\"role\":\"audio_worker\"}' \\
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
  --payload-json '{\"task_id\":\"<TASK_ID>\",\"agent_id\":\"<AGENT_ID>\",\"note\":\"handle this\"}' \\
  --wait-sec 3
```

### 6.3 Worker は memo を見る
```bash
python scripts/agent_org.py memos --to Mike
```

## 7. 作業スコープロック（soft access control）
### 7.1 lock を置く（触る前に）
```bash
export LLM_AGENT_NAME=dd-ui-01
python scripts/agent_org.py lock 'apps/ui-frontend/**' --mode no_touch --ttl-min 60 --note 'dd working'
python scripts/agent_org.py lock 'apps/ui-backend/**' --mode no_touch --ttl-min 60 --note 'dd working'
python scripts/agent_org.py locks --path apps/ui-backend/backend/main.py
```
※ `lock` は既存の active lock とスコープが交差する場合、作成を拒否する（衝突を作らないため）。必要なら `--force`（要合意）。
  lock は既定で board note を自動投稿する（不要なら `--no-announce`）。

### 7.2 lock を外す
```bash
python scripts/agent_org.py unlock <LOCK_ID>
```

## 8. 申し送り（memo）
```bash
python scripts/agent_org.py memo --to Mike --subject 'Heads up' --body 'apps/ui-backend/backend は触らないで'
python scripts/agent_org.py memos --to Mike
```

## 9. 監査（events）
```bash
tail -n 50 workspaces/logs/agent_tasks/coordination/events.jsonl
```

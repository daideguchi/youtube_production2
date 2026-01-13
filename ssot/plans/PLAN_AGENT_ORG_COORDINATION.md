# PLAN_AGENT_ORG_COORDINATION — AIエージェント組織（Orchestrator + Workers）協調の仕組み

## Plan metadata
- **Plan ID**: PLAN_AGENT_ORG_COORDINATION
- **ステータス**: Active
- **担当/レビュー**: AI Agent / dd
- **対象範囲 (In Scope)**:
  - Orchestrator の単一リース（奪取不可の座席）
  - Agent の自己識別（name / pid / heartbeat）
  - Soft access control（作業スコープロック）
  - Agent 間メモ（申し送り）とタスク割当（assignment）
  - 協調ログ（coordination events）
- **非対象 (Out of Scope)**:
  - OSレベルの強制アクセス制御（同一ユーザ上では不可能）
  - 自動で pending を完了させる常駐ワーカー群（将来）
  - UI への完全統合（将来。まずはファイルSoTを確立）
- **最終更新日**: 2025-12-29

## 1. 背景
複数の AI エージェントを同時に立ち上げて並列で作業する運用が増えたため、以下が必要になった:
- 「誰が何を担当しているか」が見える（名前/役割/稼働状態）
- 「ここは今触るな」が共有できる（ゆるいアクセス制御）
- Orchestrator を人間が任命し、途中で他エージェントに奪われない
- LLM API が落ちたら THINK MODE（agent queue）で確実に続行する（既に実装済み）

## 2. 成功条件（DoD）
- Orchestrator を **単一** に固定できる（2人目が起動できない）。
- Agent が自分の **名前** と **PID** を登録し、heartbeat で「生存」が判定できる。
- スコープロックにより「ここは作業中」が共有できる。
- Orchestrator が request を処理して:
  - `assigned_role` の付与
  - `assignment` 作成 + 対象 Agent への memo 送信
  ができる。
- すべてがファイルとして残り、UI/他ツールから参照できる（SoT）。

## 3. 仕組み（ファイルSoT）
### 3.1 協調ディレクトリ（queue_dir 配下）
既定の queue_dir: `workspaces/logs/agent_tasks`（`LLM_AGENT_QUEUE_DIR` で変更可）

```
<queue_dir>/coordination/
  events.jsonl                     # 協調イベントログ（append-only）
  memos/*.json                     # 申し送り（agent間のメモ）
  locks/*.json                     # スコープロック（soft access control）
  board.json                       # Shared Board（単一ファイル共同メモ/状態/レビュー）
  agents/*.json                    # agent registry（name/pid/heartbeat）
  assignments/*.json               # タスク割当（orchestrator→agent）
  orchestrator/
    lease.lock                     # Orchestrator の排他ロック（flock）
    state.json                     # Orchestrator 状態（pid/heartbeat）
    pid                            # Orchestrator PID（stop 用）
    inbox/req__*.json              # request inbox（他agent→orchestrator）
    outbox/resp__*.json            # request response
    processed/req__*.json          # 処理済み request
    stdout.log                     # start 時のログ
```

### 3.2 Orchestrator（奪取不可）
- `coordination/orchestrator/lease.lock` を `flock(LOCK_EX)` で保持するプロセスが **唯一の Orchestrator**。
- 二人目は `LOCK_NB` で失敗し、起動できない。
- Orchestrator の交代は **stop → start** のみ（勝手な奪取を禁止）。

※同一ユーザ権限上でのため、悪意ある直接書き換えは防げない。ここでは「事故防止」を目的とした **soft-but-robust** の運用制約を採用する。

### 3.3 Agent（自己識別 + heartbeat）
- Agent は `agents start/run/register` により `agents/*.json` を更新する。
- heartbeat により `last_seen_at` を更新し、一覧で `active/stale/dead` を判定する。
- 役割は:
  - `role`: agent 自己申告（例: worker）
  - `assigned_role`: Orchestrator が付与（衝突しないように分離）

### 3.4 タスク割当（assignment + memo）
- Orchestrator は request `assign_task` を処理して:
  - `assignments/*.json` を作成
  - 対象 agent に memo を送る（subject: `TASK ASSIGNED: <task_id>`）
- 具体的な作業対象は `workspaces/logs/agent_tasks/pending/<task_id>.json` と紐付く（`task_id` をキーにする）。

### 3.5 スコープロック（soft access control）
- `locks/*.json` を作り、スコープ（repo相対パス or glob）を宣言する。
- **重要**: zsh は `apps/ui-frontend/**` のような `**` を展開するため、glob は **必ずクォート**する（例: `'apps/ui-frontend/**'`）。

## 4. CLI（実装）
実装: `scripts/agent_org.py`

### Orchestrator
- 起動: `python scripts/agent_org.py orchestrator start --name dd-orch`
- 状態: `python scripts/agent_org.py orchestrator status`
- 停止: `python scripts/agent_org.py orchestrator stop`
- 起動（入口固定: guardrails + preflight 一括）: `python3 scripts/ops/orchestrator_bootstrap.py --name dd-orch`
- request 作成（例: 役割付与）:
  - `python scripts/agent_org.py orchestrator request --action set_role --payload-json '{"agent_id":"<AGENT_ID>","role":"audio_worker"}' --wait-sec 3`
  - `agent_name` でも可（同名があると失敗しうる）: `python scripts/agent_org.py orchestrator request --action set_role --payload-json '{"agent_name":"Mike","role":"audio_worker"}' --wait-sec 3`

### Agents
- heartbeat 起動: `python scripts/agent_org.py agents start --name Mike --role worker`
- heartbeat + board更新（標準）: `python3 scripts/ops/agent_bootstrap.py --name Mike --role worker --doing "..." --next "..." --tags ui,cleanup`
- 一覧: `python scripts/agent_org.py agents list`

### Locks / Memos
- lock:
  - `python scripts/agent_org.py lock 'apps/ui-frontend/**' --mode no_touch --ttl-min 60 --note 'dd working'`
  - `python scripts/agent_org.py lock 'apps/ui-backend/**' --mode no_touch --ttl-min 60 --note 'dd working'`
- lock audit: `python scripts/agent_org.py locks-audit --older-than-hours 6`（解除し忘れ/無期限lock点検）
- memo: `python scripts/agent_org.py memo --to Mike --subject '...' --body '...'`
- UI: `/agent-org`（API: `/api/agent-org/*`）

### Shared Board（単一ファイルで共同）
- status: `python scripts/agent_org.py board set --doing "..." --next "..." --tags ui,cleanup`
- notes: `python scripts/agent_org.py board note --topic "[Q][ui] ..." <<'EOF' ... EOF`
  - reply: `python scripts/agent_org.py board note --reply-to <note_id> --topic "[REVIEW][ui] ..." <<'EOF' ... EOF`
- ownership: `python scripts/agent_org.py board area-set <AREA> --owner <AGENT> --reviewers <csv>` / `python scripts/agent_org.py board areas`

### Preflight（並列運用のガードレール点検）
- `python3 scripts/ops/parallel_ops_preflight.py`
  - `.git` write-lock 状態
  - Orchestrator lease 状態
  - stale lock/agent heartbeat の概況
  - execpolicy（rollback禁止）が有効かの probe（失敗は警告扱い）

## 5. ログ設計
- `workspaces/logs/agent_tasks/coordination/events.jsonl` に協調系イベントを JSONL で集約（append-only）。
- 正本: `workspaces/logs/llm_usage.jsonl` / `workspaces/logs/agent_tasks/pending`
- API失敗→THINK MODE は `packages/factory_common/llm_api_failover.py` が:
  - `workspaces/logs/llm_usage.jsonl` に `api_failover_*` を追記
  - `workspaces/logs/agent_tasks/coordination/memos/*.json` へブロードキャスト（デフォルト有効）

## 6. 運用ルール（事故防止）
- 作業前に:
  - `LLM_AGENT_NAME` を設定し、pending は claim して担当者を残す（`scripts/agent_org.py` の lock/memo/board 等の write 操作では必須）
  - 触るスコープに lock を置く（最低 30min TTL）
- 複数エージェントで判断/レビュー/申し送りが必要な場合は Shared Board を正本にする（BEP-1: `ssot/ops/OPS_AGENT_PLAYBOOK.md`）。
- Orchestrator の seat は 1つ。交代は stop/start で明示する。
- request は inbox/outbox で記録されるため、勝手に上書きせず「依頼→処理→証跡」を徹底する。

## 7. 今後の拡張（Backlog）
- UI に `agents / assigned_role / assignments / locks / memos` を統合し、誰が何を担当中かを可視化する。
- pending を拾って自動処理する worker daemon 群（ただし LLM API 依存をどうするか要検討）。
- lock の衝突検知を lint/CI に組み込み（ソフト強制）。

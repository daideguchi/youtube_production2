# RUNBOOK_THINK_MODE_OPERATOR — THINK MODE（AIエージェント運用プロトコル）

## Runbook metadata
- **Runbook ID**: RUNBOOK_THINK_MODE_OPERATOR
- **ステータス**: Active
- **対象**: THINK MODE（`LLM_MODE=think`）で発生する `workspaces/logs/agent_tasks/pending/*.json`
- **想定利用者**: AIエージェント（端末操作・ファイル編集・コマンド実行ができる）
- **最終更新日**: 2025-12-12

## 1. 目的（DoD）
- 任意のパイプラインコマンドを THINK MODE で完走させる。
- pending が出たら、エージェントが runbook に従って results を投入し、再実行で前に進める。

## 2. 重要な前提
- **コマンドはエージェントが実行する**（人間は監督/指示のみ）。
- THINK MODE は「API LLM 呼び出しの代わりに pending を作って停止」する。
- 重要: **API LLM が落ちた場合も自動で THINK MODE にフォールバック**する（`LLM_API_FAILOVER_TO_THINK`、デフォルト有効）。
- 複数エージェント運用では `LLM_AGENT_NAME` を設定し、作業前に pending を **claim** して衝突を避ける。

## 3. 実行プロトコル（ループ）

### 3.1 実行（1回目）
推奨: `scripts/think.sh`（.envロード＋THINK MODE＋pending一覧/バンドル作成まで一発）

```bash
./scripts/think.sh --all-text <command> [args...]
```

`--loop` を付けると「pending が消えるまで待機→自動で再実行」になる。  
同一ターミナルで手作業する場合は **ブロックして不便** なので、基本は `--loop` なし（pending 解決→手で再実行）を推奨。

### 3.2 pending を検出したらやること
1. pending 一覧:
   - `python scripts/agent_runner.py list`
   - フォールバック/申し送りのメモ確認（任意）:
     - `python scripts/agent_org.py memos`
   - 複数エージェント運用（任意）:
     - `python scripts/agent_org.py orchestrator status`
     - `python scripts/agent_org.py agents list`
2. 1タスクずつ処理:
   - （推奨）担当を明示して claim:
     - `export LLM_AGENT_NAME=Mike`（または `python scripts/agent_runner.py --agent-name Mike ...`）
     - `python scripts/agent_runner.py claim <TASK_ID>`
   - `python scripts/agent_runner.py bundle <TASK_ID> --include-runbook`
     - `workspaces/logs/agent_tasks/bundles/<TASK_ID>.md` が作られる（Runbook + messages のスナップショット）
   - バンドルを読んで **要求された出力のみ** を作成
   - `python scripts/agent_runner.py complete <TASK_ID> --content-file /path/to/content.txt`

### 3.3 再実行（ループ継続）
- **最初に止まった元コマンド**を同じ引数で再実行する。
- まだ pending が出たら 3.2 に戻る。
- exit code 0 で完走したら終了。

## 4. 禁止事項（運用事故防止）
- pending の `messages` が要求していない “前置き/謝罪/提案/質問/メタ説明” を混ぜない
- JSON指定のタスクで JSON 以外を混ぜない

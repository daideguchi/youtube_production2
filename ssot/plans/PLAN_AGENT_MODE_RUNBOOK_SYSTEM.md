# PLAN_AGENT_MODE_RUNBOOK_SYSTEM — API LLM を AIエージェント運用に置換する仕組み

## Plan metadata
- **Plan ID**: PLAN_AGENT_MODE_RUNBOOK_SYSTEM
- **ステータス**: Active
- **担当/レビュー**: AI Agent / dd
- **対象範囲 (In Scope)**:
  - `factory_common/llm_router.py`（LLM呼び出しの agent/think-mode 追加）
  - `factory_common/llm_client.py`（script_pipeline が使う LLM呼び出しの agent/think-mode 追加）
  - `factory_common/agent_mode.py`（タスクキュー/結果キャッシュ/Runbook選択）
  - `scripts/agent_runner.py`（キュー操作CLI）
  - `configs/agent_runbooks.yaml`（task→runbook のマッピング）
  - `ssot/agent_runbooks/*`（運用Runbook）
  - `ssot/ops/OPS_ENV_VARS.md`（環境変数のSSOT追記）
- **非対象 (Out of Scope)**:
  - キュー監視デーモン（自動で pending を拾って完了させる常駐プロセス）
  - UI で pending を完結管理する機能（後続）
- **最終更新日**: 2025-12-12

## 1. 背景と目的
- API LLM の代わりに「端末操作できるAIエージェント」が推論/作業して処理を進めたい。
- タスクごとに **超詳細な指示書（Runbook）** を持ち、運用がブレないようにする。
- API/Agent をフラグで切替できるようにし、破壊的変更なしで段階導入する。

## 2. 成功条件（DoD）
- `LLM_EXEC_SLOT=3|4`（THINK/AGENT）で実行すると、LLM 呼び出しが:
  - API を呼ばず `workspaces/logs/agent_tasks/pending/` にタスク JSON を生成し停止（互換: `logs/agent_tasks/...`）
  - `workspaces/logs/agent_tasks/results/<id>.json` が存在すれば API の代わりにそれを返す（互換: `logs/agent_tasks/...`）
  - `complete → rerun` で **同じコマンドが続きから進む**
- Runbook が `ssot/agent_runbooks/` に集約され、作業者が迷わない（成果物・チェック・禁止事項が明文化）。

## 3. 仕組み（enqueue → complete → rerun）
1. パイプライン実行中に LLM が必要になる
2. agent/think-mode の場合、`pending/<task>__<hash>.json` を生成して停止
3. エージェントが Runbook に従って推論・作業し、`results/<task>__<hash>.json` を作る
4. 同じコマンドを再実行すると、結果が返されて処理が続く

## 4. 切替（環境変数）
正本: `ssot/ops/OPS_ENV_VARS.md`

- `LLM_EXEC_SLOT`（標準）:
  - `3`（デフォルト / THINK MODE）: pending を作って停止（安全デフォルトで intercept）
  - `0`（明示API）: 既存の API LLM 呼び出し
  - `4`（AGENT MODE）: pending を作って停止（明示）
- 互換/緊急用: `LLM_MODE=api|agent|think`（通常運用のロックダウンONでは停止。使うなら `YTM_EMERGENCY_OVERRIDE=1`）
- **禁止: API失敗→THINK の自動フォールバック**（失敗したら停止して報告。pending は最初から THINK/AGENT で出す）
- 担当エージェント名（agent_org/agent_runner write系では必須）:
  - `LLM_AGENT_NAME=...`（例: `LLM_AGENT_NAME=Mike`）
    - pending に `claimed_by` / `claimed_at` を自動付与
    - results に `completed_by` を保存
- 対象タスク（省略可）:
  - `LLM_AGENT_TASKS=...`（完全一致 allowlist）
  - `LLM_AGENT_TASK_PREFIXES=...`（prefix allowlist）
  - `LLM_AGENT_EXCLUDE_TASKS=...`（完全一致 blocklist）
  - `LLM_AGENT_EXCLUDE_PREFIXES=...`（prefix blocklist）
- キュー配置:
  - `LLM_AGENT_QUEUE_DIR=/path/to/dir`（既定: `workspaces/logs/agent_tasks`。互換: `logs/agent_tasks`）
- Runbook マッピング:
  - `LLM_AGENT_RUNBOOKS_CONFIG=...`（既定: `configs/agent_runbooks.yaml`）

## 5. 運用の入口
- 一発ラッパー（入口固定）: `./scripts/think.sh`
- キュー操作: `python scripts/agent_runner.py ...`
- Runbook 入口: `ssot/agent_runbooks/README.md`

## 6. スキーマ（最低限）
### pending
- `schema_version`, `id`, `created_at`, `task`, `response_format`
- `messages`（LLM互換）
- `options`（timeout等の非本質は hash から除外）
- `runbook_path`, `result_path`
- `failover?`（API失敗時の付帯情報: error/chain など）
- `claimed_by?`, `claimed_at?`（担当エージェント名/タイムスタンプ）
- `caller`, `invocation`

### results
- `schema_version`, `id`, `task`, `completed_at`, `completed_by?`, `content`, `notes?`

## 7. schema_version 運用ルール
- 破壊的に変える場合のみ `schema_version` を上げる（keys/意味が変わる、必須項目が増える、など）。
- 互換が必要な場合は:
  - 変換スクリプト（old → new）を追加し、Runbook に手順を追記する。
  - または `agent_runner` 側で旧版を読み取り対応する（互換期間を決める）。

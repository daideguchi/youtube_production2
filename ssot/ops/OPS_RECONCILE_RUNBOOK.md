# OPS_RECONCILE_RUNBOOK — Reconcile（迷子を機械的に潰す固定導線）

目的:
- 途中で落ちる/迷う原因になりがちな「SoTのズレ」「run未選択」「CapCutドラフト欠損」を、
  **決定論的な復帰コマンドだけ**で潰す。
- エージェントが独自導線（直叩き/勝手なロジック/勝手なAPI消費）に逸れないようにする。

入口（固定）:
- dry-run: `./ops reconcile --channel CHxx --video NNN`
- 実行: `./ops reconcile --channel CHxx --video NNN --llm think --run`
  - 迷わない短縮: `./ops think reconcile --channel CHxx --video NNN --run`

関連（復帰コマンド固定）:
- `ssot/ops/OPS_FIXED_RECOVERY_COMMANDS.md`

---

## 1) 入力（根拠）

Reconcile は **派生ビュー** `episode_progress` の `issues[]` を根拠にする（新しいSoTは作らない）。

- 取得: `./ops progress --channel CHxx --videos NNN --format json`（内部で同等の集計を実施）

---

## 2) 実行モデル（安定稼働のためのルール）

- デフォルトは **dry-run**（実行しない）
- `--run` を付けた時だけ実行する
- `--run` で `--video/--videos` を省略した場合は **拒否**（暴発防止）
  - 例外的に全体実行する場合のみ `--all` を明示する
- 実行時は既定で `./ops doctor` を先に実行（不要なら `--skip-doctor`）

---

## 3) issue→復帰コマンド（固定マッピング）

Reconcile は下記以外の修復は行わない（“勝手な修正”を禁止）。

### 3.1 CapCutドラフト欠損/破損
- issues: `capcut_draft_missing` / `capcut_draft_broken`
- 実行（固定）:
  1) audio final が無い場合（`audio_ready=false`）:
     - `./ops resume audio -- --llm <MODE> --channel CHxx --video NNN`
  2) ドラフト再生成:
     - `./ops resume video -- --llm <MODE> --channel CHxx --video NNN`

### 3.2 run未選択/選択runが消えている
- issues: `video_run_unselected` / `video_run_missing`
- 実行（固定）:
  - `./ops resume episode -- --channel CHxx --video NNN`

### 3.3 記帳/CSV系（自動では直さない）
- issues: `planning_stale_vs_status`, `planning_duplicate_video_rows`, `status_json_missing`
- Reconcile は **note を出すだけ**（人間の意思決定が必要）
  - 例: planning CSV 修正 / status 修復導線（`validate_status_sweep` など）

---

## 4) LLMコスト制御

`--llm` は “復帰コマンドが呼ぶ内部処理” に適用される:
- `--llm think`: 外部LLM APIを使わない（pending化→agentで埋める）
- `--llm api`: 外部LLM API（通常）
- `--llm codex`: codex exec（明示した時だけ）

---

## 5) 実行ログ（時系列）

- opsレジャー: `workspaces/logs/ops/ops_cli/ops_cli_events.jsonl`
  - `cmd=reconcile` で start/finish を残す（git情報も含む）

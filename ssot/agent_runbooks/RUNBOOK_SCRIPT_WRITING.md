# RUNBOOK_SCRIPT_WRITING — 台本生成（script_*）

## Runbook metadata
- **Runbook ID**: RUNBOOK_SCRIPT_WRITING
- **ステータス**: Legacy
- **対象**: （参照用。現運用では使わない）
- **最終更新日**: 2026-01-19

NOTE（更新: 2026-01-19）:
- これは **過去運用**の記録として残す。
- 現行方針: 台本（`script_*`）は **THINK がデフォルト**（pending）。本文生成は対話型AIエージェントが **Claude CLI（sonnet 4.5 既定。リミット時は Gemini 3 Flash Preview → `qwen -p`）/ 明示API** で仕上げる。
- 現運用の入口/手順は `ssot/agent_runbooks/RUNBOOK_JOB_SCRIPT_PIPELINE.md` と `ssot/ops/OPS_SCRIPT_PIPELINE_SSOT.md` を参照。

## 1. 目的（DoD）
- `messages` の要件に従い、台本生成に必要な **本文だけ** を出力する（余計な説明を混ぜない）。
- 出力は「次工程がそのまま読める」品質（日本語の自然さ、矛盾/破綻の排除、不要な重複の削減）。

## 2. 手順
1. `python scripts/agent_runner.py bundle <TASK_ID> --include-runbook`（標準）
2. bundle か pending を読み、要求フォーマットを確認
3. 出力を作る
   - 章立て/見出し/箇条書きなどは `messages` が指定する形式に合わせる
4. results を投入
   - `python scripts/agent_runner.py complete <TASK_ID> --content-file /path/to/content.txt`
5. 元コマンドを rerun

## 3. チェック
- `messages` で指定された制約（文字数/構造/終端トークン等）を満たす
- 「出力コンテンツだけ」になっている

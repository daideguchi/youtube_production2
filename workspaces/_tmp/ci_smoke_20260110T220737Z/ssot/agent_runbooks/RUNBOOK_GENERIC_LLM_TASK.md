# RUNBOOK_GENERIC_LLM_TASK — agent/think-mode 共通

## Runbook metadata
- **Runbook ID**: RUNBOOK_GENERIC_LLM_TASK
- **ステータス**: Active
- **対象**: すべての task（default）
- **最終更新日**: 2025-12-12

## 1. 目的（DoD）
- `pending/*.json` の `messages` と `response_format` に従い、必要な **出力コンテンツだけ** を作る。
- `results/*.json` を投入し、元コマンドの rerun で処理が続く状態にする。

## 2. 手順
1. pending を確認
   - `python scripts/agent_runner.py show <TASK_ID>`
2. `messages` が要求している形式で「出力コンテンツだけ」を作る
   - `response_format=json_object` の場合は **単一のJSONオブジェクトのみ**（前後に文章を混ぜない）
3. results を投入
   - `python scripts/agent_runner.py complete <TASK_ID> --content-file /path/to/content.txt`
4. 元コマンドを同じ引数で再実行（続きへ）

## 3. 禁止事項
- 前置き/謝罪/提案/質問/メタ説明を混ぜない（要求がある場合を除く）
- JSON指定のタスクで JSON 以外を混ぜない


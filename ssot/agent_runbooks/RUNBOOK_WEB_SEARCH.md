# RUNBOOK_WEB_SEARCH — Web検索（agent_tasks）運用プロトコル

## Runbook metadata
- **Runbook ID**: RUNBOOK_WEB_SEARCH
- **ステータス**: Active
- **対象タスク**: `web_search_*`（例: `web_search_openrouter`）
- **想定利用者**: AIエージェント / オペレーター（端末操作・ファイル編集・コマンド実行ができる）
- **最終更新日**: 2026-01-09

## 1. 目的（DoD）
- Web検索が必要な処理で `pending` が出た際に、**外部APIを叩かず**に検索結果を作成し、パイプラインを再開できる状態にする。
- 出力は **厳密なJSONのみ**（前置き・注釈・Markdown禁止）。

## 2. 入出力（契約）
入力（pending）:
- `workspaces/logs/agent_tasks/pending/<TASK_ID>.json`
  - `messages`: 検索クエリと出力形式の指示（ここが正）
  - `options._model_chain`: 参考（task_id 安定化用）。検索手段には影響しない

出力（results）:
- `workspaces/logs/agent_tasks/results/<TASK_ID>.json` の `content` に **JSON文字列**を入れる
- `content` の形式（厳密）:
  - `{"hits":[{"title":str,"url":str,"snippet":str|null,"source":str|null,"age":str|null}, ...]}`
  - `hits` は最大N件（pending の指示に従う）
  - URLは `http(s)://` のみ、重複禁止

## 3. 手順（ループ）
1) pending の確認
- `python scripts/agent_runner.py show <TASK_ID>`
- `messages` の末尾にある `query:` と `最大N件` を確認

2) 検索（手段は自由。ただしURL捏造は禁止）
- 方針: 一次情報/公式/百科事典/大手メディアなど **信頼できるソースを優先**
- 釣り見出し/まとめ/出典不明は避ける

3) JSON を作る（厳密）
- 出力は JSON “だけ”
- `snippet/source/age` は不明なら `null`（空文字は使わず `null`）
- 例:
  - `{"hits":[{"title":"...","url":"https://...","snippet":null,"source":"example.com","age":null}]}`

4) 完了登録
- JSONのみを `content.txt` に保存し、投入:
  - `python scripts/agent_runner.py complete <TASK_ID> --content-file /path/to/content.txt`

5) 再実行
- pending の `invocation.argv` をヒントに、同じコマンドを再実行して先へ進める

## 4. 禁止事項（事故防止）
- JSON以外を混ぜない（説明文・コードフェンス・箇条書き禁止）
- 実在しないURLを作らない（捏造禁止）
- `hits` 以外のキーを勝手に増やさない（契約固定）

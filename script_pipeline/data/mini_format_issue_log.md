# gpt-5-mini 改行整形まわりの失敗と修正ログ

## 概要
script_draft_format ステージで chat/completions を誤って使用したことにより、改行整形が期待通りに動かず、検証で大量の行長オーバーが発生した。以後、Responses API に固定し、chat 経路を塞いだ。

## 失敗の経緯
- 2025-xx-xx: script_draft_format に `force_chat=True` を入れてしまい、意図せず chat/completions に強制。ユーザー指示は「mini を使え」「chat を使うな」だったが、空応答回避の意図で誤設定。
- 結果: 改行指示がほぼ無視され、29 字超過が大量に発生。検証で失敗 (行長オーバーが数百行)。

## 修正内容
- runner.py
  - `script_draft_format` 呼び出しで `force_chat=False` に戻し、`force_responses=True` を追加して Responses API を強制。
  - `_call_azure_chat` で `force_responses` を最優先するロジックに統一。chat/completions へのフォールバック経路を塞いだ。
- stages.yaml
  - script_draft_format の `max_tokens` を 64000 → 12000 に下げ、タイムアウト緩和。
- format_lines_prompt
  - 箇条書き・自己チェック・短い例入りの改行専用プロンプトに整理（読点のみ追加可、29 字以内、改行は句読点 or 元改行のみ）。

## 現在の正しいリクエスト構文（mini / Responses API）
```
POST https://<endpoint>/openai/responses?api-version=2025-03-01-preview
{
  "model": "gpt-5-mini",
  "input": [
    {"role": "system", "content": "<改行ルール>"},
    {"role": "user",   "content": "<RAW_TEXT>"}
  ],
  "max_output_tokens": 12000,
  "reasoning": {"effort": "medium"},
  "text": {"verbosity": "low", "format": {"type": "text"}}
}
```
※ script_draft_format では必ず `/openai/responses` を叩く（force_responses=True）。

## 未対応/今後の確認
- 章1を Responses で再実行し、ログ URL が `/openai/responses` であることを確認したうえで、改行検証が通るかを再チェックする。

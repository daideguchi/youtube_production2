# HISTORY_incident_20260119_unapproved_qwen_model_override_followup

対象インシデント:
- `ssot/history/HISTORY_incident_20260119_unapproved_qwen_model_override.md`

## 追記（ユーザー指示）
- **ロールバックは禁止**（既存ファイルの復元を実行しない）

## 運用上の取り扱い（更新）
- 指示外モデル（例: `qwen --model ...`）で生成された出力は **採用しない**（SoTへ確定させない）
- 修正手段は **gemini CLI（3 flash）** または **qwen -p（モデル指定なし）** に限定し、同一話を再生成して置換する
- 生成はアシスタントが本文を書かず、プロンプト/検証/再試行（続き生成含む）の「調整」に徹する


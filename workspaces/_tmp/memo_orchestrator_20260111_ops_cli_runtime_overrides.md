【目的】
- LLMルーティング/温度の調整を「tracked設定ファイル編集」ではなく「実行時オーバーライド」に統一し、並列運用のドリフト事故を防ぐ。

【インシデント記録（SSOT）】
- 既に作成済み: ssot/history/HISTORY_incident_20260111_codex_unapproved_llm_task_overrides_edit.md
  - ※ ssot/history は一時ロック中のため、追記が必要なら解除後に addendum で対応。

【今回の変更（実装）】
- scripts/ops/ops_cli.py
  - P0 launcher (`./ops`) に実行時スイッチを追加:
    - `./ops --llm-slot <N> ...` → env `LLM_MODEL_SLOT=<N>`
    - `./ops --exec-slot <N> ...` → ops内だけの exec-slot 上書き（env `YTM_OPS_FORCE_EXEC_SLOT=<N>`）
    - `./ops --emergency-override ...` → env `YTM_EMERGENCY_OVERRIDE=1`
  - 方針: 台本（script pipeline）は API-only のため `--exec-slot` は無視して `LLM_EXEC_SLOT=0` を強制（明示メッセージ出す）。
- ssot/ops/OPS_LLM_RUNTIME_OVERRIDES.md
  - `./ops --llm-slot/--exec-slot/--emergency-override` の追記。

【運用上の期待値】
- `configs/llm_task_overrides.yaml` は運用中に編集しない（SSOT保護）。
- ルーティング切替は基本 `LLM_MODEL_SLOT`（数字スロット）で行う。
- task単位の温度/オプション変更が必要な場合のみ、1-run 限定で `YTM_EMERGENCY_OVERRIDE=1` を使う。


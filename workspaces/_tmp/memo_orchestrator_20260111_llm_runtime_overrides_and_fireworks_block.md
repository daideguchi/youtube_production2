【要点】
- 台本パイプラインのモデル/温度調整は「設定ファイル編集」ではなく「実行時オーバーライド」で行えるように寄せています。
- ただし現在 Fireworks 側が HTTP 412（Account suspended）になり、script 用キーは全滅 → CH06-035 の script_validation が継続不能です。

【実装（今回）】
- `scripts/ops/script_runbook.py` に task option の実行時上書きを追加（設定ファイルは触らない）
  - `--llm-task-temperature TASK=FLOAT`（repeatable）→ `LLM_FORCE_TASK_OPTIONS_JSON`
  - `--llm-task-option TASK=JSON_OBJECT`（repeatable）→ `LLM_FORCE_TASK_OPTIONS_JSON`
- Lockdown (`YTM_ROUTING_LOCKDOWN=1` デフォルト) 下では上記はデフォルト禁止。
  - 例外的に 1-run debug で使う場合は `YTM_EMERGENCY_OVERRIDE=1` を付ける前提。

【運用メモ】
- 原則: `configs/llm_task_overrides.yaml` を編集しない。
- ルーティングは slot/codes に寄せる:
  - `--llm-slot <N>` / `LLM_MODEL_SLOT=<N>`
  - `--exec-slot <N>` / `LLM_EXEC_SLOT=<N>`
- どうしても task 単位の温度/オプションが必要なケースだけ、上記の `YTM_EMERGENCY_OVERRIDE=1` + `--llm-task-*` を使う。

【現状ブロッカー: Fireworks 412】
- `script` keyring は 412(suspended) を quarantine 済みで現在 0 件。
- 注意: token-free の `--mode models` だと 200 を返す場合があり、412 の検知が漏れます。
  - 412 判定は `scripts/ops/fireworks_keyring.py check --mode chat`（1トークン）で確実。

【次アクション】
- Fireworks billing / 上限解除 or 新しい有効キーの投入（412 にならないアカウント）
- 解除後に `./ops api script resume --channel CH06 --video 035 --force-script-validation` を再開

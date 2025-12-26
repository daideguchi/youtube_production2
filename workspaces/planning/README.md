# Planning Data

- ここは **Planning（企画/進捗）SoT** を置く場所。
- 正本: `channels/CHxx.csv`（UI と `planning_store` が直接参照）。手動編集ルールは `ssot/ops/OPS_PLANNING_CSV_WORKFLOW.md` に従う。
- Script の進捗 SoT は `workspaces/scripts/CHxx/NNN/status.json`（このディレクトリではない）。
- 生成物（音声/画像/動画/ログ）をここに置かない。用途が違うものが混ざると「ゴミ判定」が破綻する。
- 追加や更新は `ssot/history/HISTORY_codex-memory.md` に記録する。

> Google Sheets との自動同期は行わない。CSV をレビューしてから commit/push する。

# log_research（ローカル scratch）

このディレクトリは「調査/復旧/検証の一時置き場」です（正本ではありません）。

固定ルール:
- 正本ログは `workspaces/logs/`（詳細: `ssot/ops/OPS_LOGGING_MAP.md`）を使う
- ここに置くのは暫定メモ・抽出物・スクショ・セッションダンプ等の“作業中の材料”のみ
- 作業が収束したら、内容は必ず `backups/_incident_archives/<repo>/<YYYYMMDD_label>/log_research/` に退避する（repo直下に残さない）
- 事象の要約/復元手順/再発防止は `ssot/history/` に1本だけ残す（巨大ログは置かない）

運用:
- 最新の退避先は `LATEST_ARCHIVE.txt`（1行）を更新する
- git で追跡するのは `README.md` と `LATEST_ARCHIVE.txt` のみ（それ以外は ignore）

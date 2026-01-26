# HISTORY_20260126_capcut_purge_guard — CapCutドラフト自動削除の事故予防（allow_purge必須化）

目的:
- **Hot=未投稿**（編集中）を守り、Macの作業停止/資産消失を防ぐ。
- 外部ストレージ（Lenovo/NAS）が不安定でも、Mac編集/生成が止まらない状態を維持する。

背景（発見）:
- LaunchAgent で `export_mover`（CapCut書き出し監視）と `capcut_purge_archived`（ドラフト削除）が常駐していた。
- 旧挙動では `--capcut-archive-mode copy` でも purge queue に enqueue され得て、`_ARCHIVED_FROM_MAC` marker があると **未投稿でもドラフトが消え得る** リスクがあった。

対処（2026-01-26）:
- `/Users/dd/doraemon_hq/ops/export_mover.py`
  - purge queue の schema を `doraemon.capcut_purge_queue.v2` に更新し、`allow_purge` を追加。
  - `--capcut-archive-mode copy` では enqueue しない（削除しない）。
  - `--capcut-archive-mode move` のみ enqueue（削除は別プロセスが実施）。即時 `rmtree` はしない（CapCut実行中の事故回避）。
- `/Users/dd/doraemon_hq/ops/capcut_purge_archived.py`
  - queue に `allow_purge=true` が明示されていないエントリは **削除しない**（policy skip）。
  - `allow_purge` の真偽は `true/1/yes/on` を明示的に truthy として解釈。

バックアップ:
- 変更前スクリプトは `/Users/dd/doraemon_hq/_tmp/backup_20260126_capcut_purge_guard/` に退避。

運用メモ:
- “未投稿のドラフトを自動で消す” のはNG。迷ったら purge 系 LaunchAgent は停止して Mac の編集を守る。
- 投稿後に容量回収したい場合のみ、**削除の明示**（`move`/`allow_purge`）を使う（手順はSSOTで別途固定）。

関連SSOT:
- `ssot/ops/OPS_CAPCUT_DRAFT_EDITING_WORKFLOW.md`
- `ssot/ops/OPS_CAPCUT_DRAFT_STORAGE_STRATEGY.md`


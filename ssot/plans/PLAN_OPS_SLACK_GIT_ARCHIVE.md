# PLAN_OPS_SLACK_GIT_ARCHIVE — SlackやりとりをGitへ“安全に”要約アーカイブ（PM Inbox）

目的:
- Slackに埋もれる「指示/意思決定/質問」を取りこぼさず、**PM運用として追える状態**にする。
- 出先（スマホ）でも **GitHub Pages Guide から読める**ようにする。
- ただし、Gitを“ゴミ箱”にしない（最小・要約・再現性）。

非目的（やらない）:
- Slackの生ログ/全トランスクリプトを git に保存しない（機微/ノイズ/容量の観点で非推奨）。
- Slackの channel_id / user_id / thread_ts などの識別子を SSOT（公開）に固定しない。
- secrets（APIキー等）を git に保存しない（redactしても “貼る” こと自体が事故源）。

方針（Recommended / 固定ロジック）:
1) Slackは一次受け（通知/会話）。**正本（SoT）は SSOT/コード/ログ**。
2) Slackからは **“要約Inbox”** のみを git に残す（= 書庫化）。
   - 形式: `ssot/history/HISTORY_slack_pm_inbox.md`
   - 各メッセージは **hash key** で識別（Slack IDは書かない）。
3) 実データ（Slack IDとの対応・raw JSON）は **workspaces/logs/** に保存（git管理しない）。
4) 取り込み→反映の順序:
   - Slack返信（dd意思決定）を取り込む → SSOT更新（DECISIONS/ops）→ 実装 → push → Slackへ報告

利用ツール:
- 送信/受信（Bot方式）: `scripts/ops/slack_notify.py`
  - thread返信取り込み: `--poll-thread ... --poll-write-memos`
  - チャンネル履歴（エラー棚卸）: `--history ... --history-grep '(error|failed|traceback)'`
- PM Inbox同期（gitへ要約保存）: `scripts/ops/slack_inbox_sync.py`（このPlanで追加）
  - Slack側にも「取り込んだ要点」を返す（任意）: `slack_inbox_sync.py sync --post-digest`（新規Inboxのみをスレへ要約返信）
- PID稼働状況の可視化（ps→Slack通知）: `scripts/ops/process_report.py`
  - 自動検出: `python3 scripts/ops/process_report.py --auto --slack`
  - 明示PID: `python3 scripts/ops/process_report.py --pid 52211 --pid 52239 --slack`

安全（必須）:
- Slack→git 取り込みは **token-like文字列を自動redact**し、本文は短く切る（長文は要約のみ）。
- `.env` や keys を Slack/Issue/Doc に貼らない（貼った時点で漏洩扱い）。

運用ループ（最小）:
1) 進捗/質問をSlackスレに投げる（`slack_notify.py --thread-ts ...`）
2) ddの返信を取り込む（`slack_notify.py --poll-thread ...`）
3) `slack_inbox_sync.py sync` で **PM Inboxを更新**（gitへ要約）
   - 任意: `--post-digest` で「取り込んだ要点」をスレへ返信（取りこぼし/見落とし防止）
4) SSOT/実装を更新 → push → Slackで報告

# HISTORY_slack_pm_inbox — PM Inbox（Slack要約; Git書庫）

目的:
- Slackに埋もれる dd の指示/決定/質問を、**取りこぼさず**に追える形にする。
- ただし、Slackの生ログや識別子（channel_id/user_id/thread_ts 等）は **gitに固定しない**。

更新:
- 生成/追記: `python3 scripts/ops/slack_inbox_sync.py sync --write-ssot`
- 運用正本: `ssot/plans/PLAN_OPS_SLACK_GIT_ARCHIVE.md`

注意（安全）:
- 本文は短く切り、token-like文字列は `[REDACTED]` に置換される。
- Slack側の一次情報（全文/文脈）はSlackで確認する（このファイルは“PM用の要約Inbox”）。

---

## Inbox（auto）
<!-- inbox:start -->
- [ ] 2026-01-10T13:31:32.140019Z key=7ad0b2b227 src=thread kind=request who=dd plain | daideguchi/youtube_production2] LLM Smoke workflow run
- [ ] 2026-01-10T12:26:04.210179Z key=ce9d2a24a2 src=thread kind=question who=dd plain | ローカル辞書は疑問。不要と思う。汎用的な修正がむずしいから個別対応してるわけでしょ？
- [ ] 2026-01-10T00:15:30.439699Z key=95bab0ac37 src=thread kind=rule who=dd plain | ローテはしない！！
- [ ] 2026-01-09T14:39:46.733219Z key=8b6ef6ee8c src=thread kind=decision who=dd plain | Aはok
<!-- inbox:end -->


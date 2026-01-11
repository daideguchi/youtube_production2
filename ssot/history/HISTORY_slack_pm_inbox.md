# HISTORY_slack_pm_inbox — PM Inbox（Slack要約; Git書庫）

目的:
- Slackに埋もれる dd の指示/決定/質問を、**取りこぼさず**に追える形にする。
- ただし、Slackの生ログや識別子（channel_id/user_id/thread_ts 等）は **gitに固定しない**。

更新:
- 生成/追記: `python3 scripts/ops/slack_inbox_sync.py sync --write-ssot`
- 任意（取り込み要約をSlackへ返信）: `python3 scripts/ops/slack_inbox_sync.py sync --write-ssot --post-digest`
- 運用正本: `ssot/plans/PLAN_OPS_SLACK_GIT_ARCHIVE.md`

注意（安全）:
- 本文は短く切り、token-like文字列は `[REDACTED]` に置換される。
- Slack側の一次情報（全文/文脈）はSlackで確認する（このファイルは“PM用の要約Inbox”）。

---

## Inbox（auto）
<!-- inbox:start -->
- [ ] 2026-01-11T07:55:05.601499Z key=1ab22abe96 src=thread kind=question who=dd plain | まず、モデル指定はコード、スロットって運用はokだよね？
- [ ] 2026-01-11T07:12:03.745109Z key=56391899fe src=thread kind=question who=dd plain | script-main-1はそれで固定でいいが、script-main-2は？　kimiだよね？
- [ ] 2026-01-11T06:54:24.149009Z key=ecfbaade1e src=thread kind=question who=dd plain | ただ、死んだ処理や、やり直したいって時はkillを積極的に許容しないとやばくない？
- [ ] 2026-01-11T04:37:31.970429Z key=78b185feef src=thread kind=request who=dd plain | 推奨でどんどん進めて。明らかに死んでるワーカーは止めてもいいよ。明らかに死んでたらね
- [ ] 2026-01-11T02:26:42.590689Z key=37de41410a src=thread kind=request who=dd plain | 30分に一回ペースくらいでポーリング的なやつ打って、slackの投稿拾って、codex execのxhighで処理させる仕掛け作ろう。これ実装すれば対話型が止まってたとしても、slackとローカルが常時接続状態になる。もちろんMacが稼働してるときだけど。もちろん非対話だからといって記憶なしに進めるのではなく、対話非…
- [ ] 2026-01-11T00:49:02.218679Z key=9c63fba7f0 src=thread kind=request who=dd plain | pid稼働に関しては、そんな情報を羅列されても、結局どの処理が今回ってるか分からない。私が把握できる形式に整理して通知してほしい
- [ ] 2026-01-11T00:47:38.029999Z key=9599c1355c src=thread kind=question who=dd plain | そういえば、gitをストレージ保管庫にする作戦を伝えてたはずだけどどうなった？長文だったから3回くらいに分けて送信してたんだけど。これ実装すれば、容量問題は解決しそうだけど、、、あとはレガシー系のロジックもgitの書庫リポにぶち込んでいけば、躊躇せずに整理が進む気もするが。
- [ ] 2026-01-10T13:31:32.140019Z key=7ad0b2b227 src=thread kind=request who=dd plain | daideguchi/youtube_production2] LLM Smoke workflow run
- [ ] 2026-01-10T12:26:04.210179Z key=ce9d2a24a2 src=thread kind=question who=dd plain | ローカル辞書は疑問。不要と思う。汎用的な修正がむずしいから個別対応してるわけでしょ？
- [ ] 2026-01-10T00:15:30.439699Z key=95bab0ac37 src=thread kind=rule who=dd plain | ローテはしない！！
- [ ] 2026-01-09T14:39:46.733219Z key=8b6ef6ee8c src=thread kind=decision who=dd plain | Aはok
<!-- inbox:end -->

<!--
  CONTACT_BOX — エージェント連絡箱（Gitで同期）

  目的:
  - 管理者(あなた) ⇄ AIエージェント間の「指示/確認/回答」を、Gitで同期して取りこぼしゼロにする
  - スマホから編集できる“連絡ノート”として使う（GitHub上で直接編集OK）

  重要:
  - APIキー/トークン/個人情報などの秘密は絶対に書かない
  - 既存行の書き換えは避け、追記(append)で運用（衝突/コンフリクト低減）
-->

# CONTACT_BOX — エージェント連絡箱（Git同期）

運用ルール（固定）:
- エージェントは作業開始前に `git pull` → 本ファイルと `reference/【消さないで！人間用】確定ロジック.md` を確認する
- 返信/進捗/質問は **ここに追記**してから push する（Slackだけに依存しない）
- 既存項目の“編集”は最小化（追記で更新履歴を残す）

---

## INBOX（管理者 → エージェント）
（ここに追記してください）

- id:
  at_utc:
  priority: P0|P1|P2
  request:
  desired_output:
  constraints:
  deadline:
  links:

---

## OUTBOX（エージェント → 管理者）
（エージェントが追記します）

- id:
  at_utc:
  by_agent:
  status: ack|in_progress|blocked|done
  summary:
  next:
  links:

---

## NOTES
- “確定ロジック”の正本: `ssot/reference/【消さないで！人間用】確定ロジック.md`

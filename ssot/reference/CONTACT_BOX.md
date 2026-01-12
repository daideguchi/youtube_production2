# CONTACT_BOX — dd ↔ agents 連絡箱（Git同期 / モバイル編集OK）

目的:
- `ssot/reference/【消さないで！人間用】確定ロジック.md` と同じ階層に「連絡箱」を置き、スマホからでも“最新の指示/回答/決定”を編集・共有できるようにする。
- Slack の取りこぼし・解釈ズレを減らす（重要事項はここにも転記して“正”を1つに寄せる）。

運用ルール（重要）:
- **追記（append-only）**：原則、過去のブロックは消さない（修正が必要なら「訂正ブロック」を追記）。
- 1件=1ブロックで書く（時刻/要件/アクション/期限/担当/関連リンク）。
- **秘密情報は書かない**（APIキー、トークン、個人情報、非公開URLなど）。
- 変更作業が絡む場合は、関連する `lock_id` / 変更ファイル / commit hash を書く（並列衝突防止）。

書式（テンプレ）:
```
[P0|P1|P2] YYYY-MM-DDTHH:MM:SSZ from=dd to=agents topic=...
- 背景:
- 依頼/決定:
- 期限:
- 受け入れ条件（Doneの定義）:
- 関連: (paths / links / lock_id / commit)
- 状態: open | doing | done | blocked
```

---

## Inbox（dd → agents）※新しい順に追記

## Replies（agents → dd）※新しい順に追記

## Decisions（確定事項の短い要約）

## Parking lot（後で）


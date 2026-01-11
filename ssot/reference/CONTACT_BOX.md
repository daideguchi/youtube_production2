# CONTACT_BOX — 人間↔AIエージェント連絡（Git同期 / mobile編集用）

目的:
- `ssot/reference/【消さないで！人間用】確定ロジック.md` と同様に、**スマホ（GitHub）から確認・編集できる**連絡ボックス。
- Slackが流速で埋もれる状況でも、**必ず残る**形で「指示/合意/確認事項」を残す。

重要ルール:
- **APIキー/個人情報/未公開URLなど secrets は絶対に書かない。**
- 追記は**末尾**へ（衝突を減らす）。過去行の書き換えは原則しない。
- 大きな仕様確定は `ssot/DECISIONS.md` にも反映する（このファイルは“連絡”であり“意思決定台帳”ではない）。

編集（モバイル）:
- GitHub上でこのファイルを開き、編集→コミット（main）する。
- ローカル側は `git pull` で取り込む（エージェントは作業前にpullして差分を読み取る）。

---

## Inbox（Human → Agents）

書式（コピペして追記）:
- ts: `YYYY-MM-DDTHH:MM:SSZ`
- from: `dd`
- priority: `P0|P1|P2`
- topic: `要件の短い見出し`
- request: `何をしてほしいか（具体）`
- definition_of_done: `完了条件（YES/NOで判定できる形）`
- deadline: `いつまで`
- notes: `補足（任意）`

### Messages


---

## Outbox（Agents → Human）

書式（追記）:
- ts: `YYYY-MM-DDTHH:MM:SSZ`
- agent: `dd-xxxx-01`
- status: `done|in_progress|blocked`
- summary: `何をしたか（1〜3行）`
- links: `commit/SSOT/ログ/URL（必要なものだけ）`
- next: `次に必要な人間判断/次アクション`

### Messages


# CONTACT_BOX — 管理者⇄AIエージェント 連絡箱（Git同期 / スマホ編集OK）

目的:
- 管理者（あなた）とAIエージェント間の「指示・確認・回答」を **Gitに残す**（Slack取りこぼし/履歴散逸を防ぐ）。
- 重要な確定事項（モデル/運用/仕様）は最終的に SSOT/DECISIONS に反映する（この箱は会話の受付口）。

運用ルール（最小）:
- 書式は厳密でなくてOK。ただし **時刻（JST or UTC）** と **誰→誰** は必ず書く。
- 1件=1ブロックで追記（上に追記でも下に追記でも可。衝突しにくい方で）。
- 秘密情報（APIキー/Token等）は **絶対に書かない**。

---

## Inbox（管理者→エージェント）

### YYYY-MM-DD HH:MM (JST) / from: dd / to: agent
- 内容:
- 期待する結果:
- 優先度: P0/P1/P2
- 期限:
- 関連リンク（SSOT/ログ/Issueなど）:

---

## Outbox（エージェント→管理者）

### YYYY-MM-DD HH:MM (JST) / from: agent / to: dd
- 受領した内容:
- 現状:
- 次アクション:
- ブロッカー:
- 反映済み（commit/PR/URL）:

### 2026-01-12 14:00 (JST) / from: agent / to: dd
- 受領した内容: 「CapCut以外の編集ソフトでも素材を共有したい / 資産はgitで扱いたい / Pagesを直感的に」
- 現状:
  - SSOT/Decision: D-020（編集ソフト非依存のEpisode Asset PackをGit追跡）を追加・反映済み
  - CLI: `scripts/ops/video_assets_pack.py` を追加（run_dir→素材束export / 外部画像ingest / 素材束→run_dir sync）
  - Pages: Script Viewerに「素材束（Git）」リンク、Snapshotに各回「素材束」リンクを追加
  - 例として `CH01-251` の素材束（images 51枚 + manifest）を生成してGitに追加済み
- 次アクション:
  - まずは必要回から `video_assets_pack.py export --write` で素材束を増やす（容量/速度を見ながら）
  - サムネの欠けは `pages_thumb_placeholders` 側（別ロック作業）で埋めて、モバイル閲覧の体験を安定化
- ブロッカー:
  - この環境から `slack.com` のDNS解決ができず、Slackへの自動投稿が失敗（outboxに退避）
- 反映済み:
  - commit: `668752cd`
  - Pages: `https://daideguchi.github.io/youtube_production2/?id=CH01-251`
  - Snapshot: `https://daideguchi.github.io/youtube_production2/snapshot/?channel=CH01&q=CH01-251`
  - Slack outbox: `workspaces/logs/ops/slack_outbox/outbox__20260112T050053Z__482882fd68.json`

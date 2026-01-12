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

### 2026-01-12 15:20 (JST) / from: agent / to: dd
- 受領した内容: 「モバイルが使いにくい / サムネが見れない / 資産はGitに載せたい」
- 現状:
  - Pages Script Viewer: 選択中エピソードのサムネを“台本ビュー”でも即表示（Hero）+ ワンタップで「サムネ/画像」へ移動
  - モバイル: Channel chips を非表示（横スクロール/重複導線を削ってシンプル化）
  - 動画内画像: raw URL一覧コピーを追加（外部編集ソフトでまとめてDLしやすい）
  - サムネ: `pages_thumb_placeholders.py --write` を実行して欠け174件を埋め、Script Viewer掲載の全回で「必ず何か表示」される状態にした
- 次アクション:
  - placeholder を実サムネへ置換する場合: `pages_thumb_previews.py --all --write`（上書きは `--overwrite`）
  - 動画内画像が未生成の回: `pages_video_images_previews.py --channel CHxx --video NNN --write`
- 反映済み:
  - commit: `8e3cf916`
  - Pages: `https://daideguchi.github.io/youtube_production2/?id=CH01-251`

# OPS_SLACK_OPS_GATEWAY — Slack→ローカル `./ops` 実行（macOS / ローカル専用）

目的:
- スマホ/外出先から、このMac上の `factory_commentary` を **安全に** 操作する（例: UI再起動、進捗確認、企画追加、台本リライト起動）。
- Slackは一次受け（会話/指示）として使い、実行はローカルで `./ops ...` を叩いて結果を返信する。

前提:
- この仕組みは **Macが起動している間だけ** 動く（ローカル実行）。
- Slack識別子（channel_id / user_id / thread_ts 等）は **git に保存しない**（LaunchAgent plist と `workspaces/logs/` のローカル state のみに保存）。

## 安全ルール（事故防止 / 強制）
- **任意コマンドの実行は禁止**。Slackから受け付けるのは allowlist だけ（`./ops` の一部固定コマンドに限定）。
- 実行者は **ddユーザーに限定**（`--dd-user` / `--allow-user`）。
- 同時実行は **1本**（ロックファイルでガード）。
- Slack返信は短く（上限あり）。詳細ログは `workspaces/logs/ops/slack_ops_loop/` を見る。
- secrets/キー/環境変数ダンプは Slack に貼らない（`OPS_ENV_VARS.md` のSlack章に従う）。

## 入口（CLI）
まずは手動で1回動作確認（dry-runで確認）:
- `python3 scripts/ops/slack_ops_loop.py run --channel <C...> --thread-ts <...> --dd-user <U...> --dry-run`

常駐（macOS / launchd; ローカル専用）:
- `python3 scripts/ops/install_slack_ops_launchagent.py --channel <C...> --thread-ts <...> --dd-user <U...> --interval-sec 1800`

## Slack側の使い方（スレ内メッセージ）
スレ内に「1行コマンド」で投げる（最初は明示コマンドのみ対応）:
- `help`
- `ui status` / `ui restart`
- `latest`
- `progress CH01`
- `idea add CH01 <working-title>`
- `script rewrite CH01 019 <instruction>`

メモ:
- コマンドは誤爆防止のため `ytm ...` プレフィックス必須（例: `ytm ui status`）。
- `script rewrite` は時間がかかることがある。結果はスレ返信で返る（ログはローカルに保存）。
- 曖昧な依頼（例: 「台本やり直して」だけ）は、必要情報（CH/番号/指示文）の追加を促す返信になる。

## 参照
- Slack通知/Inbox（別物）: `ssot/ops/OPS_ENV_VARS.md` / `ssot/DECISIONS.md#d-015p1slackの指示決定をどう取りこぼさず書庫化するgit-as-archive`
- ログ配置: `ssot/ops/OPS_LOGGING_MAP.md`

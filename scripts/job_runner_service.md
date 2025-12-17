# job_runner 常駐化サンプル（systemd / cron）

## 前提
- `<REPO_ROOT>`（このリポジトリの絶対パス）がプロジェクトルート
- `.env` をプロジェクト直下に配置済み（キー設定済み）
- Python はシステムデフォルトを想定（必要ならvenvを指定）

## systemd サービス例（ローカル用）
`/Library/LaunchDaemons/factory_job_runner.plist` (macOS LaunchDaemon例)
```
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>factory.job_runner</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>-m</string>
    <string>script_pipeline.job_runner</string>
    <string>run-loop</string>
    <string>--max-iter</string><string>60</string>
    <string>--limit</string><string>100</string>
    <string>--max-parallel</string><string>1</string>
    <string>--sleep</string><string>10</string>
  </array>
  <key>WorkingDirectory</key><string><REPO_ROOT></string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONPATH</key><string><REPO_ROOT></string>
  </dict>
  <key>StandardOutPath</key><string><REPO_ROOT>/logs/job_runner.out</string>
  <key>StandardErrorPath</key><string><REPO_ROOT>/logs/job_runner.err</string>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>60</integer> <!-- 60秒ごとに起動 -->
</dict>
</plist>
```
- 配置後、`sudo launchctl load /Library/LaunchDaemons/factory_job_runner.plist`
- 停止: `sudo launchctl unload ...`

## cron の例（毎分起動）
```
* * * * * cd <REPO_ROOT> && PYTHONPATH="<REPO_ROOT>" /usr/bin/python3 -m script_pipeline.job_runner run-loop --max-iter 60 --limit 20 --max-parallel 1 --sleep 10 >> logs/job_runner.cron.log 2>&1
```

## Render 等のホスティング
- 常駐ワーカーとして `python -m script_pipeline.job_runner run-loop --max-iter 60 --limit 100 --max-parallel 1 --sleep 10` を起動コマンドに設定。
- `.env` をRenderの環境変数として登録（SCRIPT_PIPELINE_FORCE_FALLBACK=1 を推奨）。
- 永続ストレージに `script_pipeline/data` をマウントし、logs/queueファイルを保持。

## Slack通知（任意）
- `scripts/notifications.py` に簡易Webhook送信あり。環境変数 `SLACK_WEBHOOK_URL` を設定し、ジョブ完了フック等から呼び出す。

## 運用メモ
- キュー確認: `python -m script_pipeline.job_runner list`
- 429/失敗で自動リトライしたい場合: ジョブ追加時に `--max-retries` を設定。
- データ/ログ肥大防止: `script_pipeline/data/_state/logs/*.log` と中間生成物を週次で削除する簡易スクリプトを用意すると良い。***

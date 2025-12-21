# RUNBOOK_JOB_RUNNER_DAEMON — `script_pipeline.job_runner` 常駐化（launchd / cron）

## Runbook metadata
- **Runbook ID**: RUNBOOK_JOB_RUNNER_DAEMON
- **ステータス**: Active
- **対象**: `script_pipeline.job_runner run-loop` の常駐運用（ローカル/簡易ホスティング）
- **最終更新日**: 2025-12-22

## 1. 前提
- `<REPO_ROOT>`（このリポジトリの絶対パス）がプロジェクトルート
- `.env` をプロジェクト直下に配置済み（キー設定済み）
- Python はシステムデフォルトを想定（必要なら venv の python を指定）

キュー実体（参考）:
- `workspaces/scripts/_state/job_queue.jsonl`（pending/running/completed/failed）

## 2. launchd（macOS LaunchDaemon例）
`/Library/LaunchDaemons/factory_job_runner.plist`
```xml
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
- 配置後: `sudo launchctl load /Library/LaunchDaemons/factory_job_runner.plist`
- 停止: `sudo launchctl unload /Library/LaunchDaemons/factory_job_runner.plist`

## 3. cron（毎分起動の例）
```bash
* * * * * cd <REPO_ROOT> && PYTHONPATH="<REPO_ROOT>" /usr/bin/python3 -m script_pipeline.job_runner run-loop --max-iter 60 --limit 20 --max-parallel 1 --sleep 10 >> logs/job_runner.cron.log 2>&1
```

## 4. 簡易ホスティング（例: Render）
- 起動コマンド例: `python -m script_pipeline.job_runner run-loop --max-iter 60 --limit 100 --max-parallel 1 --sleep 10`
- `.env` を環境変数として登録（必要なら `SCRIPT_PIPELINE_FORCE_FALLBACK=1`）
- 永続ストレージに `workspaces/` をマウントし、`workspaces/scripts/_state/job_queue.jsonl` と `workspaces/logs/` を保持する

## 5. Slack通知（任意）
- `scripts/notifications.py` に簡易Webhook送信あり。環境変数 `SLACK_WEBHOOK_URL` を設定する。

## 6. 運用メモ
- キュー確認: `python -m script_pipeline.job_runner list`
- 古い running を failed に倒す: `python -m script_pipeline.job_runner gc --max-minutes 120`
- 失敗を pending に戻す: `python -m script_pipeline.job_runner retry <JOB_ID>`
- ログ/中間物肥大防止: `ssot/agent_runbooks/RUNBOOK_CLEANUP_DATA.md` を参照

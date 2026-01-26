# RUNBOOK_UI_HUB_DAEMON — UI Hub（backend:8000 + frontend:3000）常駐化（Acer / Tailscale `/ui`）

## Runbook metadata
- **Runbook ID**: RUNBOOK_UI_HUB_DAEMON
- **ステータス**: Active
- **対象**: `./start.sh` で起動している UI（FastAPI + React）を常駐化し、`https://<host>.ts.net/ui/` で常時閲覧できる状態にする
- **最終更新日**: 2026-01-25

## 1. 前提
- `<REPO_ROOT>`（このリポジトリの絶対パス）が Acer 上に存在
- `.env` が `<REPO_ROOT>/.env` に存在（`scripts/check_env.py` が通る）
- backend deps: Python/venv（例: `<REPO_ROOT>/.venv/bin/python3`）
- frontend deps: `apps/ui-frontend` で `npm ci` 済み

## 1.1 最短入口（推奨: Macから1コマンド / Acerにログインしない）

目的:
- ユーザーが Acer 側で `systemctl` や `tailscale serve` を触らなくても、Macからセットアップを完結させる。

入口（dry-runが既定。最後に `--run` を付ける）:
```bash
python3 scripts/ops/bootstrap_remote_ui_hub.py \
  --host <acer_ssh_host> \
  --remote-repo-root auto \
  --workspace-root <ACER_MOUNT>/ytm_workspaces \
  --sync-env \
  --ensure-deps \
  --recover-tailscale \
  --configure-tailscale-serve \
  --configure-acer-watchdog \
  --run
```

メモ:
- sudo がパスワード要求の環境では `--sudo-mode interactive` を使う（非推奨だが初回だけは許容）。
- env は remote に `<REPO_ROOT_ON_ACER>/.env.ui_hub` としてコピーする（中身は出力しない）。
  - `--remote-repo-root auto` が見つけられない場合は、候補を追加して再実行:
    - `--remote-repo-root-candidate <REPO_ROOT_ON_ACER>`
  - `auto` の既定候補: `/srv/workspace/doraemon/repos/youtube_production2`, `/srv/workspace/doraemon/repos/factory_commentary`
  - `<ACER_MOUNT>/ytm_workspaces` が `Host is down` 等で見えない場合は、`--recover-tailscale` が tailscaled を強制再起動して復旧を試みる（sudo必須）

## 2. 手動起動（動作確認）
`/ui` 配下で配信するため、frontend は `/ui` 対応の起動スクリプトを使う。

### 2-1) Prod（推奨: 常駐/高速）
1) frontend build:
   - `cd apps/ui-frontend && npm run build:acer:gz`
2) 起動:
   - `./start.sh start --profile prod --frontend-script serve:acer --supervise`

### 2-2) Dev（開発/ホットリロード）
- 起動: `./start.sh start --profile dev --frontend-script start:acer --supervise`
- 停止: `./start.sh stop`
- 状態: `./start.sh status`
- ヘルス: `./start.sh healthcheck`（詳細: `python3 apps/ui-backend/tools/start_manager.py healthcheck --with-guards`）

確認:
- UI: `http://127.0.0.1:3000/ui/`（Acerローカル。`serve:acer` は Node 静的配信）
- API: `http://127.0.0.1:8000/api/healthz`

## 3. systemd（Linux）
テンプレ:
- `ssot/agent_runbooks/assets/ui_hub.service`

使い方:
1) `__REPO_ROOT__` を実パスに置換して `/etc/systemd/system/factory_ui_hub.service` 等へ配置
2) `sudo systemctl daemon-reload`
3) `sudo systemctl enable --now factory_ui_hub.service`
   - 既に起動済みの service に対して unit/env を更新した場合は、**`restart` しないと反映されない**（`enable --now` は既存プロセスの環境を更新しない）
   - 例: `sudo systemctl restart factory_ui_hub.service`
4) ログ: `sudo journalctl -u factory_ui_hub.service -f`

## 4. Tailscale Serve（`/ui` 公開の配線）
目的: `https://<host>.ts.net/ui/` から UI を見れるようにする（Pagesの Script Viewer とは別物）。

配線（最小）:
- `/ui/*` → frontend `http://127.0.0.1:3000`
- `/api/*` → backend `http://127.0.0.1:8000`
- `/thumbnails/assets/*` と `/thumbnails/library/*` → backend `http://127.0.0.1:8000`

推奨（Remotion preview を使う場合）:
- `/remotion/*` → frontend `http://127.0.0.1:3000`

設定は環境によりコマンドが異なるため、まず `tailscale serve status` で現状を確認し、既存の `/files` `/fleet` を壊さない形で `/ui` を差し替える。

### 4.1 Acer が不調のとき（暫定フォールバック: MacでUIを公開）
Mac 側で UI（`localhost:3000`）が動いているなら、tailnet から閲覧できるように一時公開して止血できる。

- `tailscale serve --yes --bg --https 8444 localhost:3000`
  - URL: `https://deguchimacbook-pro.tail8c523e.ts.net:8444/`

## 5. よくある事故（症状→原因）
- `https://<host>.ts.net/ui/` が **Script Viewer** になる
  - `/ui` が `docs/`（Pages用静的）を指している。Tailscale serve / reverse proxy の向き先が違うので `/ui` を frontend(3000) に差し替える。
- UIは出るが API が死ぬ
  - `/api` と `/thumbnails/*` が backend(8000) にルーティングされていない。
- APIが 502 / `database is locked` が出る（Vault/共有参照に切替後）
  - 原因: UI backend の sqlite（task/lock metrics 等）が “共有（SMB）上のパス” に置かれているとロック事故になりやすい
  - 対策: **sqlite は repo ローカル（`<REPO_ROOT>/workspaces/logs/ui/`）に固定**する（Vaultに置かない）
- UIが重い/固まる（Acerで顕著）
  - `react-scripts start`（dev server）が重い。常駐は `--profile prod` + `serve:acer`（静的配信）に切り替える。

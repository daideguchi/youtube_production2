# HISTORY_20260124_capcut_vault_mirror.md

## 目的（再掲）
- **CapCut編集体験を落とさない**（編集は Mac ローカル=Hot のまま）
- しかし「Lenovoで作業中に素材が無い」を無くすため、**保管庫（Lenovo外付け共有）へ資産を集約**
- **進捗（planning）は1箇所＝共有SSOT（mainブランチ）**
- **ミラー契約を曖昧にしない**（作成/更新=コピー、削除=削除同期）
- 例外（強制）: 台本/サムネ/生成画像は保管庫から消さない

## 結論（確定方針）
### Hot / Vault の役割分担
- **Mac（編集機 / Controller）**
  - Hot: `workspaces/**`（ローカルで生成・編集）
  - SSOT(進捗): `YTM_PLANNING_ROOT` は Vault（共有）を参照
  - Hot→Vault は **バックグラウンドで1:1ミラー**（作成/更新 + 削除同期）
- **Vault（保管庫 / Lenovo外付け共有）**
  - Vault Workspaces: `ytm_workspaces/**`（Hotの写像）
  - Asset Vault: `asset_vault/**`（グローバル素材庫。長期保持）
  - Planning SSOT: `ytm_workspaces/planning/**`（全端末で同一）

### 削除ポリシー（強制）
保管庫（Vault）側で **削除同期しない**（=残す）:
- `workspaces/scripts/**`（台本）
- `workspaces/thumbnails/assets/**`（サムネ素材）
- `workspaces/video/runs/**`（生成画像/中間生成物。再利用価値があるため Vault に残す）

それ以外は契約通り（Hotで消したら Vault も削除同期され得る）。

## 実体（マウント/パス: 現在値）
### Mac（このリポジトリ環境）
- Lenovo共有（SMB）マウント: `/Users/dd/mounts/lenovo_share_real`
- Vault workspaces: `/Users/dd/mounts/lenovo_share_real/ytm_workspaces`
- Asset vault: `/Users/dd/mounts/lenovo_share_real/asset_vault`
- Vault sentinel: `/Users/dd/mounts/lenovo_share_real/ytm_workspaces/.ytm_vault_workspaces_root.json`

### Acer（UIゲートウェイ）
- Lenovo共有（SMB/CIFS）マウント: `/srv/workspace/doraemon/workspace/lenovo_share`
- Vault workspaces: `/srv/workspace/doraemon/workspace/lenovo_share/ytm_workspaces`
- URL（Tailscale）:
  - UI: `https://acer-dai.tail8c523e.ts.net/ui/`
  - API: `https://acer-dai.tail8c523e.ts.net/api/healthz`（※HEADは405。GETでOK）
  - Files: `https://acer-dai.tail8c523e.ts.net/files/`（`ytm_workspaces/` を閲覧可）

## 実装（コード）
- Mac→Vault ミラー: `scripts/ops/workspaces_mirror.py`
  - delete-sync 既定（`--no-delete` で無効化）
  - `planning/` は既定で除外（共有SSOT上書き事故を防ぐ）
  - Vault sentinel が無い dest への delete-sync は拒否（事故防止）
  - 実行ログ: `workspaces/logs/ops/workspaces_mirror/*`
  - rsync: macOS launchd で `/usr/bin/rsync` が `Operation not permitted` を返す事象があったため、
    **`/opt/homebrew/bin/rsync` を優先**する（`YTM_RSYNC_BIN` で上書き可）
- 常駐（Mac launchd）: `scripts/ops/install_workspaces_mirror_launchd.py`
  - LaunchAgent: `~/Library/LaunchAgents/ytm.factory_commentary.workspaces_mirror.plist`
- パス診断: `scripts/ops/storage_doctor.py`

## 実行記録（この環境で完了したこと）
### 1) planning を Vault に初期投入（seed）
共有側 `ytm_workspaces/planning/` が空だったため、Macローカルから初期投入した。

- ログ: `workspaces/logs/ops/planning_seed/*`
- 最新ログ（正しいVaultへseed）: `workspaces/logs/ops/planning_seed/rsync__planning_seed__20260124T234507Z__to_lenovo_share_real.log`
- 代表コマンド（実行済み）:
  - `rsync -r workspaces/planning/patches/ <vault>/ytm_workspaces/planning/patches/`
  - `rsync -r workspaces/planning/personas/ <vault>/ytm_workspaces/planning/personas/`
  - `rsync -r --inplace workspaces/planning/templates/ <vault>/ytm_workspaces/planning/templates/`
  - 追加（不足分の補完; 実行済み）:
    - `rsync -r --inplace workspaces/planning/patches/ <vault>/ytm_workspaces/planning/patches/`
    - ログ: `workspaces/logs/ops/planning_seed/rsync__planning_seed__20260124T134510Z__patches_retry.log`

### 2) Mac→Vault ミラーを常駐導入
- 実行済み:
  - `./ops storage doctor`
  - `./ops mirror install-workspaces-launchd -- --interval-sec 600`

### 3) 状態（同期進行中）
初回は同期対象が巨大なため、**完走に時間がかかる**（差分が揃えば以後は高速化する）。

確認:
- `launchctl list | rg ytm.factory_commentary.workspaces_mirror`
- `workspaces/logs/ops/workspaces_mirror/workspaces_mirror__<stamp>.json` の `run=true` かつ `result.returncode=0` を確認（dry-runのrc=0と混同しない）
- 同期中の確認（任意）: `ps -eo pid,ppid,etime,command | rg 'workspaces_mirror.py|rsync'`

### 4) Lenovo(Vault)側ルート作成（必須）
`YTM_SHARED_STORAGE_ROOT` の直下に Vault の基本ディレクトリを作成（本運用の前提）。
- `ytm_workspaces/`（SoTミラー宛先）
- `asset_vault/`（共有素材庫）
- sentinel 作成: `./ops mirror workspaces -- --bootstrap-dest --ensure-dirs`

### 5) Acer（ゲートウェイ）を Vault SoT 参照へ切替（完了）
`factory_ui_hub.service` を **Vault SoT 参照**に切り替え、`/ui` `/api` `/files` から同じ正本を見れる状態にした。
- systemd unit を更新（`YTM_WORKSPACE_ROOT=/srv/workspace/doraemon/workspace/lenovo_share/ytm_workspaces` 等）→ `restart`
  - 実行: `python3 scripts/ops/bootstrap_remote_ui_hub.py --host acer --remote-repo-root /srv/workspace/doraemon/repos/youtube_production2 --workspace-root /srv/workspace/doraemon/workspace/lenovo_share/ytm_workspaces --run`
- `/files/ytm_workspaces` の symlink を Vault 側へ切替
  - `/srv/workspace/doraemon/workspace/ytm_workspaces -> /srv/workspace/doraemon/workspace/lenovo_share/ytm_workspaces`

付随修正（安定化）:
- workspace_root を SMB/Vault にすると、sqlite が `database is locked` を起こしやすい。
  - UI backend の sqlite（task/lock metrics）は **repoローカル**へ移動（Vaultに置かない）。

## Acer UI Hub（稼働確認済み）
2026-01-25（UTC）確認:
- `https://acer-dai.tail8c523e.ts.net/ui/` が **React UI（3000相当）** を返す（Script Viewerではない）
- `https://acer-dai.tail8c523e.ts.net/api/healthz` が `{"status":"ok", ...}` を返す
- `https://acer-dai.tail8c523e.ts.net/files/` で `ytm_workspaces/` を閲覧できる
- 監視: `https://acer-dai.tail8c523e.ts.net/files/_reports/acer_watchdog.json`（online=true）

再セットアップ（Macから、Acerにログインしない）:
- `python3 scripts/ops/bootstrap_remote_ui_hub.py --host acer --remote-repo-root auto --workspace-root /srv/workspace/doraemon/workspace/lenovo_share/ytm_workspaces --sync-env --recover-tailscale --configure-tailscale-serve --run`

## 関連SSOT/記録
- SSOT（決裁含む）: `ssot/ops/OPS_IMAGE_DDD_STORAGE_MAP_AND_APPROVAL.md`
- 共有UI運用: `ssot/ops/OPS_SHARED_WORKSPACES_REMOTE_UI.md`
- パッチ保存: `backups/patches/` 配下（capcut_vault_mirror_*）

---

## 2026-01-25 追記（Vaultのportable化 / sentinel保護 / ミラー安定化）

### 事象（破綻ポイント）
- Vault (`ytm_workspaces/**`) 内に **ホスト依存の絶対symlink**（例: `/Users/dd/mounts/...`）が混入しており、Acer 側マウントではリンクが解決できず `/files` から資産が欠ける。
- さらに、`workspaces_mirror` の main rsync が `--delete` のため **Vault sentinel（`.ytm_vault_workspaces_root.json`）を削除し得る** ことが判明（常駐が拒否/停止する）。

### 対応（コード）
- Vault整合ツールを追加: `scripts/ops/vault_workspaces_doctor.py`
  - 絶対symlink → **共有内相対symlink**へ変換
  - `thumbnails/assets` / `video/runs` 等の必須パスを作成
  - 実行ログ: `workspaces/logs/ops/vault_workspaces_doctor/*`
- `scripts/ops/workspaces_mirror.py` を安定化:
  - **同時起動防止ロック**（launchd StartInterval の重複で破綻しない）
  - Vault sentinel を rsync `--delete` から保護（`--exclude /.ytm_vault_workspaces_root.json`）
  - `video/input` は symlink/実ディレクトリ混在のため、main pass は除外し、**実ディレクトリのみ**を個別同期（symlinkはVault側のportableリンクを維持）

### 実行（このMac環境）
- Vault整合（適用）: `python3 scripts/ops/vault_workspaces_doctor.py --run`
  - レポート例: `workspaces/logs/ops/vault_workspaces_doctor/vault_workspaces_doctor__20260125T005239Z.json`
- sentinel 再生成: `python3 scripts/ops/workspaces_mirror.py --bootstrap-dest`
- launchd 再導入: `./ops mirror install-workspaces-launchd -- --interval-sec 600`

## 2026-01-25 追記（健全性チェック）

このMac環境で「運用できる状態」を満たしていることを再確認した。

- パス整合: `./ops storage doctor`
  - planning_root: `/Users/dd/mounts/lenovo_share_real/ytm_workspaces/planning`
  - vault_workspaces_root: `/Users/dd/mounts/lenovo_share_real/ytm_workspaces`
- Vault portable 整合（dry-runで差分ゼロ）:
  - `python3 scripts/ops/vault_workspaces_doctor.py`
  - レポート例: `workspaces/logs/ops/vault_workspaces_doctor/vault_workspaces_doctor__20260125T041248Z.json`
- Mac→Vault ミラー常駐の稼働ログ:
  - `workspaces/logs/ops/workspaces_mirror/workspaces_mirror__20260125T005721Z.json`

Macが滞らないための調整（既定で有効）:
- `scripts/ops/workspaces_mirror.py` は rsync を低優先度で実行し、共有が不調でも hang しにくい設定を入れた。
  - `YTM_MIRROR_NICE=10`
  - `YTM_RSYNC_TIMEOUT_SEC=60`（注: rsync --contimeout は daemon-only のため使わない）
  - 任意: `YTM_RSYNC_BWLIMIT_KBPS=<KB/s>`
  - `YTM_RSYNC_WHOLE_FILE=1`（CPU節約; binary中心のため）

## 2026-01-25 追記（CH02サムネ欠損の暫定対処: placeholder生成）

事象:
- `workspaces/thumbnails/projects.json` が参照する `workspaces/thumbnails/assets/CH02/**` の画像が欠損しており、ローカルUIでサムネが broken になって作業が滞った。

対処（暫定）:
- 欠損ファイルを「見える化」するため、**既存を上書きしない** placeholder 生成ツールを追加/実行した。
  - 実装: `scripts/ops/thumbnails_placeholders.py`
  - 実行ログ: `workspaces/logs/ops/thumbnails_placeholders/thumbnails_placeholders__20260125T051212Z.json`
  - 生成（第1段）: CH02 selected variants 76件（`00_thumb.png` ほか）
  - 追記（第2段）: CH02 all variants 36件追加（合計112件。`00_thumb_*.png` 等）
    - 実行ログ: `workspaces/logs/ops/thumbnails_placeholders/thumbnails_placeholders__20260125T054338Z.json`

注意:
- これは “作業を止めないための暫定” であり、最終的には thumbnails pipeline で実画像を生成/差し替える。

備考（Acerの生死判定）:
- Acer は「落ちない前提」だが、tailnet/LAN/SSH/HTTP は **一時的にtimeout** することがある。
- “Acerが死んでる” と断定せず、`tailscale ping` + `/ui` + `/api/healthz` + `/files/_reports/acer_watchdog.json` を併用して判断する（詳細は `ssot/ops/OPS_IMAGE_DDD_STORAGE_MAP_AND_APPROVAL.md`）。

## 2026-01-25 追記（UIエラー嵐の止血）

事象:
- backend の `/api/workspaces/thumbnails` が 500 を返すことがあり（`RuntimeError: dictionary changed size during iteration`）、UI全体が不安定化した。
- `00_thumb*.png` 欠損があるチャンネル/動画で 404 が大量発生し、ブラウザ上で “壊れた画像” になって作業が滞った。

対処:
- `apps/ui-backend/backend/routers/thumbnails_workspace.py`:
  - `refresh_channel_info()` の返り値を snapshot して 500 を止血。
- `apps/ui-backend/backend/routers/thumbnails_assets.py`:
  - 欠損 `00_thumb*.png` の placeholder返却は **任意（既定OFF）**。必要時のみ `YTM_THUMBNAILS_MISSING_PLACEHOLDER=1` で有効化（`X-YTM-Placeholder: 1`）。
- `packages/factory_common/llm_router.py`:
  - `google.generativeai` は import 時に deprecation 文面を出すため、Gemini呼び出し時のみ lazy-import/configure に変更（UI起動ログを汚さない）。

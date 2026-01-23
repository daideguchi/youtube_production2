# OPS_SHARED_WORKSPACES_REMOTE_UI — 共有Workspaces（SoT）で “どこからでも同じUI” を見る

目的:
- どの端末（Mac / iPhone / 別PC）から `https://<acer>.ts.net/ui/` を開いても、**同じSoT（台本/サムネ/進捗）** が見える状態を作る。
- 「別PCで `./start.sh` を打たない」運用を成立させる（= URLアクセスだけで完結）。

非目的:
- GitHub Pages（`docs/`）に “運用UI（3000）” を載せること（Pagesは静的ビューア用途に限定）。
- 双方向同期の一般論（ここでは **SoTの置き場を1箇所に固定**する）。

前提:
- SoT は `workspaces/**`（コード側SSOT: `factory_common.paths.workspace_root()` / env `YTM_WORKSPACE_ROOT`）。
- “共有Workspaces（SoT）” は Acer の `/files/ytm_workspaces/` に固定する（tailnet内のみ）。

---

## 0) 目標形（完成状態）

- **共有SoT**（Workspacesの正本）:
  - Acer 実体: `/srv/workspace/media/ytm_workspaces`
  - Files公開: `/files/ytm_workspaces/`（= `https://acer-dai.tail8c523e.ts.net/files/ytm_workspaces/`）
- **UI（いつもの3000）**: `https://acer-dai.tail8c523e.ts.net/ui/`
- **API**: `https://acer-dai.tail8c523e.ts.net/api/`
- 書き込み（進捗更新/一部編集）は UI→API 経由で SoT に反映され、他端末はリロードで追従する。

---

## 1) 工程（セットアップ手順）

### Step 1: Acer（常駐ハブ）側で “共有SoTの置き場” を確定

1. SoT実体ディレクトリを作る（なければ）:
   - `/srv/workspace/media/ytm_workspaces/`
2. `/files` 公開ルート配下に見えるようにする（推奨: symlink）:
   - `/srv/workspace/doraemon/workspace/ytm_workspaces -> /srv/workspace/media/ytm_workspaces`
3. `/files/ytm_workspaces/` がブラウザで開けることを確認:
   - `https://acer-dai.tail8c523e.ts.net/files/ytm_workspaces/`

### Step 2: AcerのUI常駐プロセスを “共有SoT” に向ける

- systemd（`factory_ui_hub.service`）に `YTM_WORKSPACE_ROOT=/srv/workspace/media/ytm_workspaces` を入れる。
- これにより、UIバックエンドは `workspaces/**` を **共有SoT** として読む/書く。
- ヘルスチェック:
  - `https://acer-dai.tail8c523e.ts.net/api/healthz`

### Step 3: Mac（HQ）から “共有SoT” に書く方法を決める（どちらか一択）

結論: **Macが生成する資産（台本/サムネ/音声など）を共有SoTへ入れない限り、Acer UIで正確に見えない**。

#### 方式A（推奨）: Macの実行も `YTM_WORKSPACE_ROOT` を共有SoTに揃える（= 二重SoTを作らない）

- Mac側で Acer の `ytm_workspaces` をローカルに “マウント” できる形を用意する（SSHFS/SMB/NFSなど、環境に合わせて選ぶ）。
- Macで `YTM_WORKSPACE_ROOT=<マウント先>/ytm_workspaces` を設定して、通常通りパイプラインを実行する。
- これで生成物は最初から共有SoTへ出るため、同期工程が不要になる。

#### 方式B（暫定）: Macローカル `workspaces/` → Acer共有SoTへ rsync 同期（= 同期忘れリスクあり）

- 1回目は “初期移行”（大容量）として夜間に回す。
- 以降は差分同期（定期/手動）を回す。

注意（重要）:
- 方式Bは、Acer UIで進捗を更新した直後に Mac ローカルを上書き同期すると **巻き戻し** が起きる。  
  → 進捗/企画（planning）は “共有SoTだけ” を正として運用する（ローカルを正本にしない）。

---

## 2) 工程（初期移行のチェックリスト）

最低限、UIで “資産が見える” ために必要なSoT:
- `workspaces/planning/**`（企画CSV/Persona等）
- `workspaces/scripts/**`（status.json / assembled*.md）
- `workspaces/thumbnails/**`（特に `thumbnails/assets/**`）

用途に応じて追加:
- `workspaces/audio/**`（wav/srt）
- `workspaces/video/**`（runs, previews）

---

## 3) 受け入れ基準（「できた」の定義）

1. どの端末からでも `https://acer-dai.tail8c523e.ts.net/ui/` が開ける（モバイル含む）。
2. UIで進捗を更新すると、別端末でリロードしても同じ進捗が表示される。
3. 台本（`assembled_human.md` / `assembled.md`）がUIで読める。
4. サムネ画像（`workspaces/thumbnails/assets/**`）がUIで表示できる。

---

## 4) トラブルシュート（よくある原因）

- UIで台本/サムネが見えない:
  - 共有SoT（`/files/ytm_workspaces`）にそのファイルが存在しない（= Mac側の資産が未移行/未同期）。
- 進捗が端末間でズレる:
  - “ローカルworkspaces” と “共有SoT” の二重運用になっている（方式Aへ寄せる）。
- UIが遅い:
  - dev server ではなく prod 配信（静的 + gzip）で常駐させる（関連: `ssot/ops/OPS_UI_WIRING.md`）。

---

## 関連SSOT

- UI配線/公開: `ssot/ops/OPS_UI_WIRING.md`
- ディレクトリ正本/SoT: `ssot/ops/OPS_REPO_DIRECTORY_SSOT.md`
- 共有ストレージ（L1退避）: `ssot/ops/OPS_SHARED_ASSET_STORE.md`（※本書は “SoT自体の置き場固定” が主題）

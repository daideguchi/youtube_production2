# OPS_SHARED_WORKSPACES_REMOTE_UI — 共有Workspaces（SoT）で “どこからでも同じUI” を見る

目的:
- どの端末（Mac / iPhone / 別PC）から `https://<acer>.ts.net/ui/` を開いても、**同じSoT（台本/サムネ/進捗）** が見える状態を作る。
- 「別PCで `./start.sh` を打たない」運用を成立させる（= URLアクセスだけで完結）。

非目的:
- GitHub Pages（`docs/`）に “運用UI（3000）” を載せること（Pagesは静的ビューア用途に限定）。
- 双方向同期の一般論（ここでは **SoTの置き場を1箇所に固定**する）。

前提:
- SoT は `workspaces/**`（コード側SSOT: `factory_common.paths.workspace_root()` / env `YTM_WORKSPACE_ROOT`）。
- “共有Workspaces（SoT）” の実体は **共有ストレージ上**に置き、Acer はそれをマウントしてUI/APIを提供する（Acer自身のローカルディスクに固定しない）。
  - 重要: `https://<acer>.ts.net/files/...` は **「AcerがWeb公開しているファイル閲覧」**であり、実体がAcer内蔵ディスクとは限らない（SMB/NFS/外付け等のマウントでもよい）。
  - 現状の前提（インフラ）: “共有ストレージ” は Lenovo 外付け（SMB共有）を第一候補にする（Acerはマウントしてゲートウェイになる）。

---

## 0) 目標形（完成状態）

- **共有SoT**（Workspacesの正本）: `<shared_storage>/ytm_workspaces/`
  - Acer: `<acer_mount>/ytm_workspaces/` にマウントされている（AcerはUI/APIの“入口”）
  - Files公開（任意・デバッグ用）: `/files/ytm_workspaces/`（= `https://<acer>.ts.net/files/ytm_workspaces/`）
- **UI（いつもの3000相当）**: `https://<acer>.ts.net/ui/`
- **API**: `https://<acer>.ts.net/api/`
- 書き込み（進捗更新/一部編集）は UI→API 経由で SoT に反映され、他端末はリロードで追従する。

---

## 0.1) 決裁（迷子防止）: SoTと“最終成果物保管庫”を混同しない

- SoT（正本）: `ytm_workspaces/`（= `YTM_WORKSPACE_ROOT` が指す先）
  - ここに台本/サムネ/進捗が集約される（＝UIが見る“状態”）
- 最終成果物のbytes保管庫（任意）: `uploads/<namespace>/...`（= `YTM_SHARED_STORAGE_ROOT/uploads/<namespace>`）
  - これは “SoT” ではない（L1退避・監査・容量対策の置き場）

運用上の約束:
- ユーザーは Acer を手で触らない前提。Acerの常駐化/配線は **Macからコードで完結**させる（Runbook/スクリプトに寄せる）。

---

## 0.2) `ytm_workspaces/` の中身（何が保存される想定？）

`ytm_workspaces/` は **repo の `workspaces/` と同じ階層**（= `factory_common.paths.workspace_root()`）として扱う。

代表例:
- `planning/`: 進捗・企画（channels CSV / patches / personas など）
- `scripts/CHxx/NNN/`: 台本（`assembled*.md` / status 等）
- `thumbnails/`: サムネ系
  - `thumbnails/assets/**`: 画像素材（UIで表示する前提）
  - `thumbnails/projects.json` など: 採用/生成のメタ（存在する場合）
- `audio/`: 音声系
  - `audio/final/CHxx/NNN/`: **確定WAV/SRT**（Lenovo編集時の差し替えに必須）
  - `audio/tts/**`: 中間/合成系（運用に応じて）
- `video/`: 動画・画像生成系
  - `video/runs/<run_id>/`: 生成run（Flux画像/ログ/成果物など。工程の証跡）
- そのほか（運用で使うもの）: `episodes/`, `channels/`, `notes/`, `research/`, `logs/`, `_scratch/` など

重要:
- CapCutドラフト本体は `ytm_workspaces/` に置かない（Hot/Cold運用に分離。関連: `ssot/ops/OPS_CAPCUT_DRAFT_STORAGE_STRATEGY.md`）。

---

## 1) 工程（セットアップ手順）

### Step 1: 共有ストレージ（正本）を決める（Acerではなく“ストレージ側”）

要点:
- UIは「どこでもURLで見る」が目的なので、**SoTの実体（bytes）は共有ストレージに寄せる**必要がある（Acerの内蔵に閉じると、Mac/他端末とSoTが分岐しやすい）。
- 共有ストレージの候補:
  - Lenovo 外付け（SMB共有）: 容量確保・常時稼働の前提に合う（推奨）
  - Mac直下を正本にしてAcerへ同期: “同期忘れ/巻き戻し” が起きやすいので避ける（暫定のみ）

#### 推奨: 共有SoTルート（workspacesの正本）を1つ作る

パス名は未確定でもよいが、契約として「このディレクトリが `workspaces/**` の正本」と決める。

例（いずれもOK）:
- `<SHARE_ROOT>/ytm_workspaces/`
- `<SHARE_ROOT>/uploads/factory_commentary/ytm_workspaces/`（namespaceで切る）

重要:
- SoTは repo 配下に置かない（`<repo>/workspaces` を正本にすると二重化しやすい）
- `YTM_WORKSPACE_ROOT` だけを切替点にする（コードは `factory_common.paths` を通す）

### Step 2: Acer（常駐ハブ）側で “共有SoTをマウント” し、参照先を固定する

- Acerで共有ストレージをマウントし、`YTM_WORKSPACE_ROOT=<acer_mount>/ytm_workspaces` を設定する（systemd `factory_ui_hub.service`）。
- これにより、UIバックエンドは `workspaces/**` を **共有SoT** として読む/書く（どの端末から見ても一致）。
- ヘルスチェック:
  - `https://<acer>.ts.net/api/healthz`

### Step 3: Mac（HQ）も “同じ共有SoT” を参照する（Aが本命 / Bは暫定）

結論: **Macが生成する資産（台本/サムネ/音声など）を共有SoTへ入れない限り、Acer UIで正確に見えない**。

#### 方式A（推奨）: Macの実行も `YTM_WORKSPACE_ROOT` を共有SoTに揃える（= 二重SoTを作らない）

- Mac側で “共有ストレージ（Lenovo外付け等）” をローカルにマウントできる形を用意する（SMB/NFS/SSHFSなど、環境に合わせて選ぶ）。
- Macで `YTM_WORKSPACE_ROOT=<マウント先>/ytm_workspaces` を設定して、通常通りパイプラインを実行する。
- これで生成物は最初から共有SoTへ出るため、同期工程が不要になる。

#### 方式B（暫定）: Macローカル `workspaces/` → Acer共有SoTへ rsync 同期（= 同期忘れリスクあり）

- 1回目は “初期移行”（大容量）として夜間に回す。
- 以降は差分同期（定期/手動）を回す。

注意（重要）:
- 方式Bは、Acer UIで進捗を更新した直後に Mac ローカルを上書き同期すると **巻き戻し** が起きる。  
  → 進捗/企画（planning）は “共有SoTだけ” を正として運用する（ローカルを正本にしない）。

##### 方式B-1（推奨）: planning だけ共有SSOTに固定（巻き戻し防止）

“進捗はmainブランチ” の考え方。  
Macローカルに資産が残っていても、**planning は共有を正本**にしておけば「どの端末から見ても進捗が一致」する。

- Mac/Acer ともに `planning` を同じ場所に固定:
  - `YTM_PLANNING_ROOT=<SHARE_ROOT>/ytm_workspaces/planning`
  - `YTM_WORKSPACE_ROOT` は各ホスト都合でOK（Macローカルでもよい）
- 同期（rsync）は planning を除外して回す（planning を上書きしない）
- 点検: `./ops storage doctor`（paths/env を全員で共通理解できる）

例（planning を除外して同期; dry-run → 本番）:
```bash
rsync -a --stats --dry-run \
  --exclude 'planning/**' \
  workspaces/ \
  "<SHARE_WORKSPACES_ROOT>/"

rsync -a --stats \
  --exclude 'planning/**' \
  workspaces/ \
  "<SHARE_WORKSPACES_ROOT>/"
```

##### 方式B-2（推奨）: “ミラーをコード化” してバックグラウンド常駐（作成=コピー / 削除=削除同期）

ユーザー要件:
- Macローカルで生成/更新された `workspaces/**` は **保管庫（共有）へ即コピー**される
- Macローカルで削除されたファイルは **保管庫側も削除**される（= 1:1ミラー）
- ただし planning は “mainブランチ” として共有SSOTを守るため、既定ではミラー対象から除外（上書き事故防止）
- 指示: 台本（scripts）とサムネ（thumbnails/assets）と生成画像（video/runs）は **消さない**（ミラーでも削除しない）

実装（dry-runが既定）:
- 初回（安全策）: `./ops mirror workspaces -- --bootstrap-dest --ensure-dirs`
  - 保管庫 `ytm_workspaces/` に sentinel（`.ytm_vault_workspaces_root.json`）を作る（delete-sync事故防止）
- 1回だけ同期:
  - dry-run: `./ops mirror workspaces`
  - 本番: `./ops mirror workspaces -- --run`
  - 既定: `planning/` は除外（`--include-planning` で含められる）
  - 既定: delete同期ON（`--no-delete` でOFF）
  - 宛先: `YTM_VAULT_WORKSPACES_ROOT`（= 共有の `ytm_workspaces/`）または `--dest-root <path>`
- 常駐（Mac launchd）: `./ops mirror install-workspaces-launchd -- --interval-sec 120`
  - 解除: `./ops mirror install-workspaces-launchd -- --uninstall`

##### 初期移行コマンド例（Mac側・dry-run→本番の順で）
（`<SHARE_WORKSPACES_ROOT>` は共有の `ytm_workspaces/` 実体へ置換）

```bash
# 1) dry-run（削除を含めるなら --delete を付ける前に必ず確認）
rsync -a --stats --dry-run \\
  workspaces/ \\
  \"<SHARE_WORKSPACES_ROOT>/\"

# 2) 本番（必要なら --delete。怖ければ最初は付けない）
rsync -a --stats \\
  workspaces/ \\
  \"<SHARE_WORKSPACES_ROOT>/\"
```

---

## 2) 工程（初期移行のチェックリスト）

最低限、UIで “資産が見える” ために必要なSoT:
- `workspaces/planning/**`（企画CSV/Persona等）
- `workspaces/scripts/**`（status.json / assembled*.md）
- `workspaces/thumbnails/**`（特に `thumbnails/assets/**`）

用途に応じて追加:
- `workspaces/audio/**`（wav/srt）
- `workspaces/video/**`（runs=生成画像, previews）

---

## 3) 受け入れ基準（「できた」の定義）

1. どの端末からでも `https://<acer>.ts.net/ui/` が開ける（モバイル含む）。
2. UIで進捗を更新すると、別端末でリロードしても同じ進捗が表示される。
3. 台本（`assembled_human.md` / `assembled.md`）がUIで読める。
4. サムネ画像（`workspaces/thumbnails/assets/**`）がUIで表示できる。

---

## 4) 性能（速度）の考え方

結論:
- 体感速度の主因は「端末→Acerまでのネットワーク（Tailscaleの経路/回線）」と「AcerのCPU/メモリ」と「Acer→共有ストレージのI/O（SMB等）」。
- 共有SoTがAcerローカルでも、共有ストレージ（マウント）でも、**“巨大ディレクトリ全探索” をUI/APIがやる設計だと遅い**。探索を前提にしない（index/manifest/明示クエリに寄せる）。

現状の前提（高速化の要点）:
- UI（React）は prod（静的配信）+ gzip + immutable cache で配信する（dev serverは禁止）。
- Planning APIは “巨大ディレクトリ全探索” をしない（必要なら query で明示的にONにする）。

注意:
- Mac側が共有SoTを “ネットワークマウント” して重い生成を走らせると、ローカル実行より遅くなることがある。  
  その場合は「Macはローカル生成→rsync差分同期」か、「重い処理をLenovo/常駐側へ寄せる」を検討する。

---

## 5) トラブルシュート（よくある原因）

- UIで台本/サムネが見えない:
  - 共有SoT（`YTM_WORKSPACE_ROOT` が指す正本）にそのファイルが存在しない（= Mac側の資産が未移行/未同期）。
- 進捗が端末間でズレる:
  - “ローカルworkspaces” と “共有SoT” の二重運用になっている（方式Aへ寄せる）。
- UIが遅い:
  - dev server ではなく prod 配信（静的 + gzip）で常駐させる（関連: `ssot/ops/OPS_UI_WIRING.md`）。

---

## 関連SSOT

- UI配線/公開: `ssot/ops/OPS_UI_WIRING.md`
- ディレクトリ正本/SoT: `ssot/ops/OPS_REPO_DIRECTORY_SSOT.md`
- 共有ストレージ（L1退避）: `ssot/ops/OPS_SHARED_ASSET_STORE.md`（※本書は “SoT自体の置き場固定” が主題）
- CapCutドラフト資産（Hot/Warm/Cold）: `ssot/ops/OPS_CAPCUT_DRAFT_STORAGE_STRATEGY.md`

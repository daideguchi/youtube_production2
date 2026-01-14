# OPS_GH_RELEASES_ARCHIVE — GitHub Releases を大容量アーカイブ置き場にする（manifest運用）

目的:
- `.mp4/.wav/.zip` などの **重い実体**を Git の履歴に入れずに退避し、必要時に復元できる状態にする。
- 退避物の同一性（改ざん/欠損）を **SHA256 で検証可能**にする。
- “どこに何があるか” を repo 内の **manifest（目録）**で検索できるようにする。

非目的:
- パイプラインの SoT（`workspaces/**`）を GitHub Releases に置き換えること（これは **オプションの退避**であり、標準フローではない）。
- ローカルの自動削除（削除判断は人間/Orchestrator側で行う）。

---

## 0) 正本（SoT）

- 目録（追記型）: `gh_releases_archive/manifest/manifest.jsonl`
- 検索用 index（生成物）:
  - `gh_releases_archive/index/latest.json`
  - `gh_releases_archive/index/by_tag/tag_<TAG>.json`

---

## 0.1) UI（Pages）

- 一覧UI: `/archive/`（GitHub Pages）
  - データ: `gh_releases_archive/index/latest.json`（tracked）
  - Pages は `docs/` のみデプロイするため、Pages生成時に index を `docs/archive/gh_releases_archive/index/latest.json` にミラーする（`/archive/` はミラー側を読む）。
    - Pages workflow は `gh_releases_archive/**` の変更でも走る（manifest/index更新がUIへ反映される）。
  - ここは **目録**。実体（.mp4/.wav/.zip/.tgz）は GitHub Releases assets。
  - 空の場合は未投入（= `./ops archive ... --push --run` をまだ実行していない）。

---

## 1) 実行入口（CLI）

- 統一入口: `./ops archive release --help`
- 実体: `python3 scripts/ops/release_archive.py --help`

必要条件:
- `gh` がインストール済みでログイン済み: `gh auth status`
  - チェック: 出力に `Logged in to github.com` と `Active account: true` がある
- Releases assets の運用制限: **1ファイル < 2GiB**
  - 本ツールは自動で分割する（既定 chunk size: 1.9GB）

---

## 2) 使い方

### 2.1 push（投入: upload + manifest追記）

```bash
# まず dry-run（分割/ハッシュ計算まで。Releases/manifest は触らない）
./ops archive release push "/path/to/file" \
  --note "CH23 最終レンダー" \
  --tags "channel:CH23,type:movie,stage:final" \
  --dry-run

# 実行（Releases に upload → manifest 追記 → index 更新）
ARCHIVE_REPO="OWNER/REPO" \
./ops archive release push "/path/to/file" \
  --note "..." \
  --tags "..."
```

### 2.1.1 例: Episode Asset Pack（画像束）の書庫化

目的:
- 投稿済みの run_dir を削除しても、再利用したい画像束（`0001.png...`）を残す。
- 大きい実体は Releases へ、検索性は manifest（tags）へ寄せる。

入口固定（1コマンド）:
```bash
# dry-run（計画だけ表示。何も書かない）
./ops archive episode-asset-pack --channel CHxx --video NNN --push

# 実行（Asset Pack確定 → tgz生成 → Releasesへpush）
ARCHIVE_REPO="OWNER/REPO" \
./ops archive episode-asset-pack --channel CHxx --video NNN --push --run

# 容量対策（Releasesへpush後、ローカルpack/bundleを削除）:
ARCHIVE_REPO="OWNER/REPO" \
./ops archive episode-asset-pack --channel CHxx --video NNN --push --delete-pack-dir --delete-local-bundle --run

# 注意: pack_dir に git 追跡ファイルがある場合、誤削除防止のため `--delete-pack-dir` は停止する。
#       容量のために意図的に消す場合のみ `--force-delete-pack-dir` を追加する（repoがdirtyになる）。
```

分解コマンド（参考）:
```bash
# 1) run_dir から Asset Pack へ確定（番号固定）
./scripts/with_ytm_env.sh python3 scripts/ops/video_assets_pack.py export --channel CHxx --video NNN --write --overwrite

# 2) 1ファイルへ固める（tar.gz）
tar -C workspaces/video/assets/episodes -czf /tmp/episode_asset_pack__CHxx-NNN.tgz CHxx/NNN

# 3) Releases へ投入（manifest追記 + index更新）
ARCHIVE_REPO="OWNER/REPO" \
./scripts/with_ytm_env.sh python3 scripts/ops/release_archive.py push /tmp/episode_asset_pack__CHxx-NNN.tgz \
  --note "episode asset pack (images) CHxx-NNN" \
  --tags "type:episode_asset_pack,channel:CHxx,video:NNN"
```

### 2.2 pull（復元: download + 検証 + 結合）

```bash
./scripts/with_ytm_env.sh python3 scripts/ops/release_archive.py pull "A-YYYY-MM-DD-0001" \
  --outdir "/path/to/restore"
```

### 2.2.1 Episode Asset Pack（画像束）の復元（入口固定）

目的:
- Releases 書庫 → ローカルへ **download + sha検証 + tgz展開** までを 1 コマンドで完結する。
- `manifest.jsonl` の tags（`type/channel/video`）から **最新の archive_id を検索**できるようにする。

```bash
# dry-run（計画だけ表示）
./ops archive episode-asset-pack-restore --archive-id A-YYYY-MM-DD-0001

# 実行（download + extract）
./ops archive episode-asset-pack-restore --archive-id A-YYYY-MM-DD-0001 --run

# CH/動画から検索（manifest tags）
./ops archive episode-asset-pack-restore --channel CHxx --video NNN --run

# （オプション）Asset Pack へ戻す（既存dirがある場合は停止。上書きは --overwrite-pack）
./ops archive episode-asset-pack-restore --channel CHxx --video NNN --write-pack --run
```

出力:
- 既定の展開先: `/tmp/ytm_restore/unpacked/<archive_id>/`
- tgz 内は `{CHxx}/{NNN}/...` の構造を前提とする

### 2.3 list（一覧/検索）

```bash
./scripts/with_ytm_env.sh python3 scripts/ops/release_archive.py list --query "CH23"
./scripts/with_ytm_env.sh python3 scripts/ops/release_archive.py list --tag "stage:final"
```

---

## 3) 命名規則（固定）

- archive_id: `A-YYYY-MM-DD-####`（JST日付 + 4桁連番）
- release_tag: `arch-YYYY-MM-DD`（JST日付で日単位のバケット）
- asset 名:
  - 分割なし: `<ARCHIVE_ID>__<SANITIZED_NAME>__full.bin`
  - 分割あり: `<ARCHIVE_ID>__<SANITIZED_NAME>__part-0001.bin` ...

---

## 4) 設定（env / CLI）

- `ARCHIVE_REPO`（標準）: `OWNER/REPO`
  - `--repo OWNER/REPO` で上書き可
  - 未指定時は `git remote origin` から推測（失敗する場合がある）
- `CHUNK_SIZE_BYTES`（省略可）:
  - `--chunk-size-bytes` で上書き可
  - 既定: 1,900,000,000 bytes（2GiB未満運用）

---

## 5) 注意（必須）

- manifest は repo 管理されるため、`note/tags/original.path` に **秘匿性が高い情報を入れない**。
- 失敗時の中途半端状態を避けるため、pushは `--dry-run` → 本番 の順で行う。
- 復元は chunk sha256 → 結合 → original sha256 の順で検証する（自動）。

# OPS_GH_RELEASES_ARCHIVE — GitHub Releases を大容量アーカイブ置き場にする（manifest運用）

目的:
- `.mp4/.wav/.zip` などの **重い実体**を Git の履歴に入れずに退避し、必要時に復元できる状態にする。
- 退避物の同一性（改ざん/欠損）を **SHA256 で検証可能**にする。
- “どこに何があるか” を repo 内の **manifest（目録）**で検索できるようにする。

非目的:
- パイプラインの SoT（`workspaces/**`）を GitHub Releases に置き換えること（これは **任意の退避**であり、標準フローではない）。
- ローカルの自動削除（削除判断は人間/Orchestrator側で行う）。

---

## 0) 正本（SoT）

- 目録（追記型）: `gh_releases_archive/manifest/manifest.jsonl`
- 検索用 index（生成物）:
  - `gh_releases_archive/index/latest.json`
  - `gh_releases_archive/index/by_tag/tag_<TAG>.json`

---

## 1) 実行入口（CLI）

- `python3 scripts/ops/release_archive.py --help`
- 推奨（`.env` 読み込み + PYTHONPATH整備）:
  - `./scripts/with_ytm_env.sh python3 scripts/ops/release_archive.py ...`

必要条件:
- `gh` がインストール済みでログイン済み: `gh auth status`
- Releases assets の運用制限: **1ファイル < 2GiB**
  - 本ツールは自動で分割する（既定 chunk size: 1.9GB）

---

## 2) 使い方

### 2.1 push（投入: upload + manifest追記）

```bash
# まず dry-run（分割/ハッシュ計算まで。Releases/manifest は触らない）
./scripts/with_ytm_env.sh python3 scripts/ops/release_archive.py push "/path/to/file" \
  --note "CH23 最終レンダー" \
  --tags "channel:CH23,type:movie,stage:final" \
  --dry-run

# 実行（Releases に upload → manifest 追記 → index 更新）
ARCHIVE_REPO="OWNER/REPO" \
./scripts/with_ytm_env.sh python3 scripts/ops/release_archive.py push "/path/to/file" \
  --note "..." \
  --tags "..."
```

### 2.2 pull（復元: download + 検証 + 結合）

```bash
./scripts/with_ytm_env.sh python3 scripts/ops/release_archive.py pull "A-YYYY-MM-DD-0001" \
  --outdir "/path/to/restore"
```

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

- `ARCHIVE_REPO`（推奨）: `OWNER/REPO`
  - `--repo OWNER/REPO` で上書き可
  - 未指定時は `git remote origin` から推測（失敗する場合がある）
- `CHUNK_SIZE_BYTES`（任意）:
  - `--chunk-size-bytes` で上書き可
  - 既定: 1,900,000,000 bytes（2GiB未満運用）

---

## 5) 注意（必須）

- manifest は repo 管理されるため、`note/tags/original.path` に **秘匿性が高い情報を入れない**。
- 失敗時の中途半端状態を避けるため、pushは `--dry-run` → 本番 の順で行う。
- 復元は chunk sha256 → 結合 → original sha256 の順で検証する（自動）。

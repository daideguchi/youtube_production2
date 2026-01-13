# OPS_VIDEO_ASSET_PACK — 編集ソフト非依存の「エピソード資産束」（Git追跡）

目的:
- CapCut以外（Vrew等の画像編集/動画編集ソフト）でも、同じ“素材セット”から動画制作できるようにする。
- Slack/口頭で「どの画像をどれに差す？」の迷いをなくし、**Web（raw/Pages）から誰でも参照・ダウンロード**できる状態にする。

非目的:
- `workspaces/video/runs/**`（run_dir）をGit追跡することはしない（ローカル実行状態/巨大化/差分ノイズのため）。

---

## 1) 正本（SoT）: Episode Asset Pack（Git追跡）

**配置（正本）**:
- `workspaces/video/assets/episodes/{CHxx}/{NNN}/`

**中身（固定）**:
- `images/0001.png` …（1-based・4桁ゼロ埋め。SRTのセグメント順に対応）
- `audio/CHxx-NNN.wav`（編集ソフト側の音声 / 省略可）
- `subtitles/CHxx-NNN.srt`（編集ソフト側の字幕 / 省略可）
- `manifest.json`（生成メタ。スキーマ/件数/生成元runなど）

命名ルール:
- 画像は **必ず `0001.png` 形式**（`safe_image_swap` / 目視 / 人間運用で迷わないため）。
- 画像の“内容”は自由（AI生成/手動編集/スクショ加工など）。ただし **index（番号）は固定**。

---

## 2) 使い分け（2ルートを両立）

### Route A: CapCut（既存主線）
- CapCutドラフトは `workspaces/video/runs/{run_id}/` を作って進める（`auto_capcut_run`）。
- 画像差し替えは `safe_image_swap --swap-only` を使う（キャッシュ破壊=確実反映）。

### Route B: CapCutを使わない編集ソフト（新ルート）
- `workspaces/video/assets/episodes/{CH}/{NNN}/` を **Webから取得**して、編集ソフトへ投入。
  - 画像: `images/0001.png...`
  - 音声: `audio/*.wav`
  - 字幕: `subtitles/*.srt`
- 編集ソフト側で「画像スライド/挿入」するだけで動画が作れる。

---

## 3) 生成/更新（CLI）

エクスポート（run_dir → Asset Pack）:
```bash
python3 scripts/ops/video_assets_pack.py export --channel CH01 --video 220 --include-audio --write
```

（オプション）Asset Pack → run_dir の images へ反映（CapCut差し替え前の下準備）:
```bash
python3 scripts/ops/video_assets_pack.py sync-to-run --run <run_dir> --channel CH01 --video 220 --apply
```

---

## 4) Web/モバイルでの確認

- Pagesの「動画内画像プレビュー」は `docs/media/video_images/**` を参照する（軽量jpg）。
- Asset Pack（原寸/原本）をWebから取得したい場合は `raw.githubusercontent.com` 経由で参照する。
  - 例: `.../workspaces/video/assets/episodes/CH01/220/images/0001.png`

---

## 5) 画像書庫（再利用/容量対策; オプション）

目的:
- `workspaces/video/runs/**`（run_dir）や `workspaces/video/_archive/**` を削除/退避しても、再利用したい画像を **書庫**として残せる状態にする。
- 本repoのGit履歴を巨大化させずに、大きい実体（画像束）を退避できるようにする。

固定ルール:
- run_dir は SoT ではない（Git追跡しない）。
- 書庫の単位は **Episode Asset Pack**（`workspaces/video/assets/episodes/{CHxx}/{NNN}/`）。
  - 画像は `images/0001.png...` の **番号固定**で保存する（後から再利用しやすい）。
- 書庫の実体は次のどちらかに置く（用途で選ぶ）:
  1) GitHub Releases 書庫（標準）: `ssot/ops/OPS_GH_RELEASES_ARCHIVE.md`
  2) 外部SSD（オプション）: `YTM_OFFLOAD_ROOT` 配下（パイプラインは参照しない）

### 5.1 書庫化（run_dir → Asset Pack → アーカイブ）

入口固定（1コマンド）:
```bash
# 前提:
# - `--push` を使う場合は `gh auth status` が Logged in を表示すること
# - `ARCHIVE_REPO="OWNER/REPO"` を指定する（または release_archive が origin から推測できる状態）
# - `--offload` を使う場合は `YTM_OFFLOAD_ROOT` を設定する
#
# dry-run（計画だけ表示。何も書かない）
./ops archive episode-asset-pack --channel CHxx --video NNN --push --offload

# 実行（Asset Pack確定 → tgz生成 → (optional) Releasesへpush → (optional) 外部SSDへ退避）
ARCHIVE_REPO="OWNER/REPO" \
./ops archive episode-asset-pack --channel CHxx --video NNN --push --offload --run

# 容量対策（ローカルpackを削除）:
ARCHIVE_REPO="OWNER/REPO" YTM_OFFLOAD_ROOT="/Volumes/SSD/ytm_offload" \
./ops archive episode-asset-pack --channel CHxx --video NNN --push --offload --delete-pack-dir --run

# 注意: pack_dir に git 追跡ファイルがある場合、誤削除防止のため `--delete-pack-dir` は停止する。
#       容量のために意図的に消す場合のみ `--force-delete-pack-dir` を追加する（repoがdirtyになる）。
```

分解コマンド（参考）:
1) Asset Pack へエクスポート（番号付け + manifest 生成）:
```bash
./scripts/with_ytm_env.sh python3 scripts/ops/video_assets_pack.py export --channel CHxx --video NNN --write --overwrite
```

2) 1ファイルに固める（tar.gz）:
```bash
tar -C workspaces/video/assets/episodes -czf /tmp/episode_asset_pack__CHxx-NNN.tgz CHxx/NNN
```

3-A) GitHub Releases 書庫へ投入（manifestで検索できる形）:
```bash
ARCHIVE_REPO="OWNER/REPO" \
./ops archive release push /tmp/episode_asset_pack__CHxx-NNN.tgz \
  --note "episode asset pack (images) CHxx-NNN" \
  --tags "type:episode_asset_pack,channel:CHxx,video:NNN"
```

3-B) 外部SSDへ退避（ローカルのみ）:
```bash
mkdir -p "$YTM_OFFLOAD_ROOT/episode_asset_pack/CHxx/"
mv /tmp/episode_asset_pack__CHxx-NNN.tgz "$YTM_OFFLOAD_ROOT/episode_asset_pack/CHxx/"
```

### 5.2 UIでの確認（Archive Vault）

目的:
- 「どのエピソードの Asset Pack を書庫化したか」を **UIで即確認**できるようにする（探す/見落とす事故を減らす）。

入口:
- UI: `/archive/`（サイドバー: `書庫`）

見方（固定）:
- 一覧は `gh_releases_archive/manifest/manifest.jsonl` / `gh_releases_archive/index/latest.json` の **目録**。
- `type:episode_asset_pack` で絞り込む（tag filter）。
- `channel:CHxx` / `CHxx-NNN` で検索する（query）。

復元（固定）:
- UIの `Copy restore cmd` を実行する（`release_archive.py pull` + `tar -xzf ...`）。
- 注意: 復元は `gh` 認証が必要（`gh auth status`）。

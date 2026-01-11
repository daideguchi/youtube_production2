# gh_releases_archive/ — GitHub Releases を“重い物置き”にする（v1）

目的: 動画/音声/zip などの大きいファイルを Git の履歴に入れず、GitHub Releases assets に退避して復元できるようにする。

このディレクトリは「目録（manifest/index）」だけを持ち、実体（.mp4/.wav/.zip など）は Releases に置く。

## 前提
- `gh` がインストール済みでログイン済み: `gh auth status`
- GitHub Releases asset は **1ファイル < 2GiB** 運用（既定 chunk=1.9GB）

## 目録（この repo 内）
- `gh_releases_archive/manifest/manifest.jsonl`: 追記型（1行=1アーカイブ）
- `gh_releases_archive/index/latest.json`: 直近 N 件（生成物）
- `gh_releases_archive/index/by_tag/tag_<TAG>.json`: tag 別（生成物）

## 使い方
### 1) 投入（push）
```bash
# dry-run（分割/ハッシュ計算まで。Releases/manifest は触らない）
./scripts/with_ytm_env.sh python3 scripts/ops/release_archive.py push "/path/to/file" \
  --note "CH23 最終レンダー" \
  --tags "channel:CH23,type:movie,stage:final" \
  --dry-run

# 実行（Releases に upload → manifest に追記 → index 更新）
ARCHIVE_REPO="daideguchi/youtube_production2" \
./scripts/with_ytm_env.sh python3 scripts/ops/release_archive.py push "/path/to/file" \
  --note "CH23 最終レンダー。元データ削除予定。" \
  --tags "channel:CH23,type:movie,stage:final"
```

### 2) 復元（pull）
```bash
./scripts/with_ytm_env.sh python3 scripts/ops/release_archive.py pull "A-YYYY-MM-DD-0001" \
  --outdir "/path/to/restore"
```

### 3) 検索/一覧
```bash
./scripts/with_ytm_env.sh python3 scripts/ops/release_archive.py list --query "CH23"
```

## 注意
- `note` / `tags` / `original.path` は manifest に残る（= git管理される）ため、秘匿性が高い情報は入れない。
- このツールは元ファイルを削除しない（削除は自分で判断して実施）。

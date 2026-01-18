# Script Viewer (GitHub Pages)

`workspaces/scripts/**/assembled.md` を **ブラウザで閲覧・コピー**するための静的ページです。

公開ページ:
- `/m/` : Mobile Start（スマホ用の入口; 推奨）
- `/ep/` : Episode Pages（台本/音声/サムネ/画像）
- `/` : Script Viewer（検索/コピー）
- `/snapshot/` : Production Snapshot（企画/進捗）
- `/guide/` : SSOT Guide（全体像/運用/モデル方針をスマホで学習）
- `/archive/` : 書庫（GitHub Releases）

ポイント:
- 台本本文は複製せず、GitHub の raw URL（`raw.githubusercontent.com`）から参照します。
- 事前に `data/index.json` を生成します（台本の一覧・タイトル・パス）。
- 「音声」タブは `workspaces/scripts/**/audio_prep/*`（TTS準備テキスト）を参照します（音声ファイル自体は gitignore のため対象外）。
- 「サムネ」タブは `workspaces/thumbnails/projects.json` を参照します。
  - `workspaces/thumbnails/assets/**` は容量が大きく gitignore のため、Pagesでは直接表示できません。
  - Pagesで“画像として”見たい場合は、公開用プレビューを `docs/media/thumbs/**` に生成して commit/push します（下記）。

## ローカル確認（推奨）

repo ルートで:

```bash
python3 scripts/ops/pages_script_viewer_index.py --write
python3 scripts/ops/pages_thumb_previews.py --all --write
python3 -m http.server 8009
```

ブラウザ:
- `http://localhost:8009/docs/`
- `http://localhost:8009/docs/m/`
- `http://localhost:8009/docs/ep/`
- `http://localhost:8009/docs/snapshot/`
- `http://localhost:8009/docs/guide/`

## GitHub Pages

このリポジトリは GitHub Pages の公開ルートとして `./docs` を使います（= `docs/` が正本）。

デプロイ:
- GitHub Actions（推奨/現行）: `.github/workflows/pages_script_viewer.yml`
  - Settings → Pages → Build and deployment → Source: **GitHub Actions**
  - 反映対象ブランチ: `main`

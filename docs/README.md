# Script Viewer (GitHub Pages)

`workspaces/scripts/**/assembled.md` を **ブラウザで閲覧・コピー**するための静的ページです。

公開ページ:
- `/` : Script Viewer
- `/snapshot/` : Production Snapshot（企画/進捗）
- `/guide/` : SSOT Guide（全体像/運用/モデル方針をスマホで学習）

ポイント:
- 台本本文は複製せず、GitHub の raw URL（`raw.githubusercontent.com`）から参照します。
- 事前に `data/index.json` を生成します（台本の一覧・タイトル・パス）。

## ローカル確認（推奨）

repo ルートで:

```bash
python3 scripts/ops/pages_script_viewer_index.py --write
python3 -m http.server 8009
```

ブラウザ:
- `http://localhost:8009/docs/`
- `http://localhost:8009/docs/snapshot/`
- `http://localhost:8009/docs/guide/`

## GitHub Pages

このリポジトリは GitHub Pages の公開ルートとして `./docs` を使います（= `docs/` が正本）。

デプロイ:
- GitHub Actions（推奨/現行）: `.github/workflows/pages_script_viewer.yml`
  - Settings → Pages → Build and deployment → Source: **GitHub Actions**
  - 反映対象ブランチ: `main`

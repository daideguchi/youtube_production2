# Script Viewer (GitHub Pages)

`workspaces/scripts/**/assembled.md` を **ブラウザで閲覧・コピー**するための静的ページです。

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
- `http://localhost:8009/pages/script_viewer/`

## GitHub Pages

Actions で `pages/script_viewer/` を deploy する想定です（workflow: `.github/workflows/pages_script_viewer.yml`）。

初回のみ GitHub 側で Pages を有効化してください:
- Settings → Pages → Build and deployment → Source: **GitHub Actions**


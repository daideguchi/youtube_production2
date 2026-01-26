# HISTORY_20260125_ui_error_storm_resolution.md

## 目的
- UIバックエンドのログが「エラー嵐」に見える状態を **破綻しない形で収束**させる。
- ユーザー要望（最重要）:
  - **プレースホルダーを勝手に返さない**（欠損は欠損として扱う）
  - **Macの作業を絶対に滞らせない**

関連SSOT（正本）:
- 保存先/配線/決裁: `ssot/ops/OPS_IMAGE_DDD_STORAGE_MAP_AND_APPROVAL.md`

---

## 観測された事象（ユーザー提示ログ）
- 起動時に `google.generativeai` の deprecation メッセージが大量に出る（ノイズ）。
- `/api/workspaces/thumbnails` が 500:
  - `RuntimeError: dictionary changed size during iteration`
- `/thumbnails/assets/CHxx/.../00_thumb*.png` が 404 大量（アクセスログが荒れる）。
- さらに実態として、CH02 の `00_thumb*.png` が **赤枠プレースホルダー画像**で埋まっていた
  （「壊れた」ではなく **誤った代替**。ユーザー要望で禁止）。

---

## 原因と対応

### 1) `/api/workspaces/thumbnails` の 500
原因:
- `refresh_channel_info()` が返す dict が **プロセスグローバル**で、他リクエストで更新され得る。
- その dict を `.items()` で走査中にサイズが変わり例外化。

対応:
- `list(channel_info_map.items())` で **スナップショット**してから走査する（同一リクエスト内の整合性を優先）。

影響:
- 500 が止まり、UIのサムネWS一覧が安定。

---

### 2) `/thumbnails/assets/...` の 404 / プレースホルダー混入について
重要:
- 404 は「画像が壊れている」ではなく、**そのパスにファイルが見つからない**という意味。
- ここで **勝手にプレースホルダーに差し替えるのは禁止**（ユーザー要望）。

現状整理（このMac環境で確認）:
- CH02 は `00_thumb*.png` がプレースホルダーで埋まり、UI上は「存在しているのに正しいサムネが見えない」状態だった。
- CH22 は `workspaces/thumbnails/assets/CH22/` が空で、`00_thumb.png` が未配置のため 404 になる（仕様通り）。

対応（CH02の復旧）:
- **サムネCompiler（layer_specs）で再生成**して、プレースホルダーを実画像で上書きした。
  - `./.venv/bin/python scripts/thumbnails/build.py build --channel CH02 --force`
  - `./.venv/bin/python scripts/thumbnails/build.py build --channel CH02 --thumb-name 00_thumb_3.png --force`
  - 2案（CH02/083-085 のみ）:
    - `./.venv/bin/python scripts/thumbnails/build.py build --channel CH02 --videos 083 084 085 --thumb-name 00_thumb_1.png --force`
    - `./.venv/bin/python scripts/thumbnails/build.py build --channel CH02 --videos 083 084 085 --thumb-name 00_thumb_2.png --force`

追加対応（CH02/001-005 legacy 名の実画像化）:
- `workspaces/thumbnails/projects.json` は `001_aura_CH02.png` 等を参照しているため、ここも **実画像で置き換え**が必要。
- layer_specs を追加して再生成 → `00_thumb.png`/`00_thumb_1.png` へ同期:
  - 追加: `workspaces/thumbnails/compiler/layer_specs/ch02_image_prompts_legacy_v1.yaml`
  - 登録: `workspaces/thumbnails/templates.json` の `layer_specs.registry`
  - 実行例:
    - `./.venv/bin/python scripts/thumbnails/build.py build --channel CH02 --engine layer_specs --videos 001 --thumb-name 001_aura_CH02.png --image-prompts-id ch02_image_prompts_legacy_v1 --force`
    - `cp -f workspaces/thumbnails/assets/CH02/001/001_aura_CH02.png workspaces/thumbnails/assets/CH02/001/00_thumb.png`
    - （002-005も同様）

---

### 3) 「欠損サムネのプレースホルダー返却」について（強制: 既定OFF）
方針:
- **既定OFF**（通常運用では使わない）。
- 必要な場合のみ **環境変数で明示的にON**:
  - `YTM_THUMBNAILS_MISSING_PLACEHOLDER=1`

備考:
- ON時も「欠損が治った」わけではなく、緊急時の *stop-gap*（ログ/UI崩れを止めるだけ）。

---

### 4) `GET /api/workspaces/thumbnails/{channel}/{video}/variants` の 405
原因:
- ルートが POST/PATCH のみで、UI（または手動アクセス）が GET を呼ぶと 405 になる。

対応:
- GET で variants 一覧を返すエンドポイントを追加（UIのノイズ削減）。

---

### 5) `/api/workspaces/thumbnails` が遅すぎる（Acer/Vaultでタイムアウト→502）
原因:
- overview API が「全チャンネル」について、`workspaces/thumbnails/assets/**` を **フルスキャン**していた。
- Vault（SMB）ではディレクトリ走査が高コストで、**120秒級**の応答時間になり得る。

対応（破綻しない設計=既定で重い処理をしない）:
- Disk variants のマージと YouTube プレビュー取得を **既定OFF** にし、必要時のみ opt-in:
  - `GET /api/workspaces/thumbnails?include_disk_variants=1`
  - `GET /api/workspaces/thumbnails?include_youtube_previews=1`
- さらに disk variant 収集は `rglob()` を廃止し、動画ディレクトリ直下のみ走査（compiler中間生成物を見ない）。

効果（実測）:
- Acer ローカル: `http://127.0.0.1:8000/api/workspaces/thumbnails` が **~3秒**で 200
- Tailnet 経由: `https://acer-dai.tail8c523e.ts.net/api/workspaces/thumbnails` が **~5秒**で 200

## すぐ確認できるチェック（人間/エージェント向け）
（※パスは SSOT の「Mac（編集機）: workspace_root / planning_root / vault_root」を参照）

1) 実ファイルの存在確認:
- `ls -la workspaces/thumbnails/assets/CH02/107/00_thumb.png`

2) APIが返すか（ローカルUIバックエンド起動中）:
- `curl -I http://127.0.0.1:<PORT>/thumbnails/assets/CH02/107/00_thumb.png`

3) 500が消えたか:
- `curl -sS http://127.0.0.1:<PORT>/api/workspaces/thumbnails | head`

---

## 次アクション（必要時のみ）
- 「CH22 でもサムネを見たい」なら:
  - `workspaces/thumbnails/assets/CH22/<NNN>/00_thumb.png` を正規配置する（生成/書き出し/コピーの手段は別SSOTに従う）。
- 起動ログの `google.generativeai` ノイズが残る場合:
  - 古いプロセス/古いコードで起動している可能性が高いので、UIバックエンドを再起動して再確認。

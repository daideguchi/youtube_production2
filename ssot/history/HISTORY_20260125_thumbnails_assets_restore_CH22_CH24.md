# HISTORY_20260125_thumbnails_assets_restore_CH22_CH24.md

## 目的
- UI の `/thumbnails`（および planning 一覧）で **サムネが 404 になり作業が止まる**事象を解消する。
- **プレースホルダー禁止**（“壊れた画像”は作らない）。実体が無い場合は「正規の生成/合成」で埋める。
- Acer（常駐UI）/Mac（ローカルUI）で **同じ資産を参照**できる状態に戻す。

## 事象（ユーザー報告）
- `GET /thumbnails/assets/CH24/{015..030}/{NNN}_kobo_best.png` が大量に 404
- `GET /thumbnails/assets/CH22/{041..060}/00_thumb.png` が大量に 404
- `/api/workspaces/thumbnails` が重く、タイムアウト/502 になりやすい

## 原因（現物確認ベース）
- CH24:
  - `workspaces/thumbnails/assets/CH24/**` は **ディレクトリだけ存在し、PNG 実体が無い**状態。
  - 保管庫（`ytm_workspaces/thumbnails/assets/CH24/**`）も空。
  - `archive/mac_assets/.../workspaces/thumbnails/assets/CH24/**` も空（`. _*` の AppleDouble があるのみ）。
- CH22:
  - `archive/mac_assets/.../CH22/{041..060}` は `compiler/` のみで `00_thumb.png` が無い（=未生成/生成途中）。
  - そのため UI が期待するパスが 404 になるのは仕様通りだが、運用としては「見られない」ので復旧が必要。

## 対応（NO placeholders）

### 1) CH24: kobo_best を Planning から再生成
- 生成スクリプト: `scripts/ops/ch24_kobo_best_rebuild.py`
  - 入力 SoT: `planning/channels/CH24.csv`（shared planning_root）
  - 出力:
    - `workspaces/thumbnails/assets/CH24/{NNN}/{NNN}_kobo_best.png`
    - `workspaces/thumbnails/assets/CH24/kobo_text_layer_spec_30.json`
  - 併せて `workspaces/thumbnails/projects.json` の CH24 timestamp を更新（UI の cached 404 を bust）
- ログ:
  - `workspaces/logs/ops/thumbnails_ch24_kobo_best_rebuild/ch24_kobo_best_rebuild__20260125T110229Z.json`

### 2) CH22: Gemini 枠死のため bg をローカル生成 → 合成のみ（skip-generate）
- 背景のローカル生成（10_bg のみ）:
  - `workspaces/logs/ops/thumbnails_restore/ch22_seed_bg__20260125T110331Z.json`
- 合成（文字合成のみ。背景生成は行わない）:
  - `./.venv/bin/python scripts/thumbnails/build.py build --channel CH22 --videos 041..060 --skip-generate`

### 2-b) CH22: 031..040 は video/runs の実画像を背景(10_bg)として採用 → skip-generate 合成
ユーザー認識（「31以降は作られていた」）に対し、`thumbnails/assets/CH22/031..040` には実体が無く 404 になっていた。
一方で `workspaces/video/runs/CH22-0XX_capcut_v1/images/*.png` は存在するため、
それを `10_bg.png` として取り込み、**合成のみ**で `00_thumb.png` を復旧した（placeholderは使わない）。

- 背景取り込みログ:
  - `workspaces/logs/ops/thumbnails_restore/ch22_seed_bg_from_runs__20260125T115650Z.json`
- 合成ログ:
  - `workspaces/logs/ops/thumbnails_restore/ch22_build_skip_generate__031_040__20260125T115709Z.log`
- 保管庫同期ログ:
  - `workspaces/logs/ops/thumbnails_restore/rsync__CH22_031_040_to_vault__20260125T115933Z.log`
  - `workspaces/logs/ops/thumbnails_restore/rsync__CH22_038_040_to_vault__20260125T120229Z.log`（timeout後の継続分）

### 3) 保管庫（ytm_workspaces）へ同期
- `workspaces/thumbnails/assets/CH24/**` → `ytm_workspaces/thumbnails/assets/CH24/**`
  - `workspaces/logs/ops/thumbnails_restore/rsync__CH24_to_vault__20260125T110423Z.log`
- `workspaces/thumbnails/assets/CH22/**` → `ytm_workspaces/thumbnails/assets/CH22/**`
  - `workspaces/logs/ops/thumbnails_restore/rsync__CH22_to_vault__20260125T110519Z.log`
- `workspaces/thumbnails/projects.json` → `ytm_workspaces/thumbnails/projects.json`
  - `workspaces/logs/ops/thumbnails_restore/rsync__projects_json_to_vault__20260125T110834Z.log`

### 4) Acer UI の性能改善（/api/workspaces/thumbnails）
- 修正: `apps/ui-backend/backend/app/thumbnails_disk_variants.py`
  - `rglob('*')` を廃止し、**top-level のみ iterdir()** で収集（SMB 上での分単位遅延を抑止）
- Acer へ反映し `factory_ui_hub.service` を再起動
- 目安（Acer localhost）:
  - 旧: ~116s
  - 新: ~25s（さらに改善余地あり。tailnet 経由では 6s 程度の実測）

## 再発防止（要点）
- “missing を赤枠 placeholder で埋める” 運用は採用しない（ユーザー要求）。
- CH24 の `kobo_best` は **ファイル直参照**であり、実体が無いと必ず 404 になるため、
  Planning SoT から再生成できる入口（`scripts/ops/ch24_kobo_best_rebuild.py`）を保持する。
- 共有保管庫（Vault）へは `thumbnails/assets/**` を必ず同期し、Acer/UI が 1 箇所を見れば済む状態を維持する。

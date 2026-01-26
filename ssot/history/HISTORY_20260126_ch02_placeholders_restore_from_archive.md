# HISTORY_20260126_ch02_placeholders_restore_from_archive.md

## 概要
CH02 の selected サムネ実体が **placeholder（赤枠）** に置換され、UI から見ると
「画像が消えた/404嵐」のように見える状態になっていた。

ユーザー要件:
- **placeholder 生成は禁止**（勝手に代替画像を作らない）
- **元々存在していた実体を探し、正規パスへ戻す**
- Mac の作業（編集体験）を止めない

結論:
- Lenovo 外付け（D:）の `archive/mac_assets` に **オリジナルが残っていた**
- `ytm_workspaces/thumbnails/assets` 側の placeholder を **archive→vault で復元**
- Mac の Hot 側も **同一ファイルを取得**して placeholder を除去

## 影響範囲
- 対象: **CH02 の selected**（`workspaces/thumbnails/projects.json` の `selected_variant_id` が指す `image_path`）
- 対象数: 76（placeholder 判定: 先頭ピクセルが `(20, 6, 6, 255)`）

## 原因（確定）
- `ytm_workspaces/thumbnails/assets/CH02/**` の一部が placeholder 画像に上書きされていた
- しかし Lenovo 外付けの **アーカイブ**に、正しい実体が残っていた

### placeholder 判定（再現用）
Hot 側の画像を読み込み、先頭ピクセルが以下に一致するものを placeholder と判定:
- `(20, 6, 6, 255)`（赤枠の典型値）

## 重要パス（正本）
### Lenovo（Windows）
- Share 入口（固定）: `C:\doraemon_share`
- 実体（外付け）: `D:\doraemon_ext`
- Archive（オリジナル格納）:
  - `D:\doraemon_ext\archive\mac_assets\10_YouTube_Automation\factory_commentary\workspaces\thumbnails\assets\...`
- Vault（UI が参照する正規置き場）:
  - `D:\doraemon_ext\ytm_workspaces\thumbnails\assets\...`

### Acer（Ubuntu / UIゲートウェイ）
- Vault マウント（参照先）:
  - `/srv/workspace/doraemon/workspace/lenovo_share/ytm_workspaces/thumbnails/assets/...`
- Archive（参照可）:
  - `/srv/workspace/doraemon/workspace/lenovo_share/archive/mac_assets/10_YouTube_Automation/factory_commentary/workspaces/thumbnails/assets/...`

### Mac（編集機）
- Hot:
  - `workspaces/thumbnails/assets/...`

## 実施内容（実行ログあり）
### 1) placeholder 対象リスト作成（Mac）
- 出力:
  - `workspaces/logs/ops/thumbs_restore_from_archive/placeholder_selected_CH02__*.txt`

### 2) archive → vault 復元（Lenovo）
- 方針: **archive に存在するものを vault へ上書きコピー**
  - `src = D:\doraemon_ext\archive\mac_assets\...\assets\<rel>`
  - `dst = D:\doraemon_ext\ytm_workspaces\thumbnails\assets\<rel>`
- 結果: `COPIED 76 / MISSING_SRC 0`
- 代表ログ:
  - Mac 側実行ログ: `workspaces/logs/ops/thumbs_restore_from_archive/lenovo_restore_from_archive__*.log`
  - Lenovo 側レポート: `C:\doraemon_share\_reports\thumbs_restore_from_archive__*.log`

### 3) vault → Hot 反映（Mac）
- 方針: Mac 側 placeholder を **scp で vault の実体に置換**
- 結果: `COPIED 76 / MISSING 0`、`placeholder_remaining 0`
- 代表ログ:
  - `workspaces/logs/ops/thumbs_restore_from_archive/mac_pull_from_lenovo_scp__*.log`

## 検証（OK）
- Mac Hot: `selected 778 / missing_selected 0`
- Acer Vault: `selected 778 / missing_selected 0`
- サンプル:
  - `https://acer-dai.tail8c523e.ts.net/thumbnails/assets/CH02/107/00_thumb.png`
    - HTTP 200 / `size_download=38394`（placeholder 時より縮小）

## 運用ルール（再掲）
- **placeholder 生成（`thumbnails_placeholders.py --run`）は禁止**
- 「無いものは無い」として扱い、実体が archive にある場合のみ復元する

## TODO（SSOT反映）
本件の復元手順（archive→vault→Hot）を SSOT に追記する必要あり。
ただし SSOT 本体ファイル `ssot/ops/OPS_IMAGE_DDD_STORAGE_MAP_AND_APPROVAL.md` はロック中のため、
解除後に追記する（本 HISTORY を参照）。


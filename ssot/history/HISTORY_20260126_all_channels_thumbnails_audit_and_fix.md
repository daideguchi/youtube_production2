# HISTORY_20260126_all_channels_thumbnails_audit_and_fix.md

## 目的
サムネ資産（`workspaces/thumbnails/assets/**`）が「消えた/404」とならないよう、
`projects.json` の **selected** が指す実体の存在を **全チャンネル**で監査し、
不整合を安全に解消する（placeholder 生成は禁止）。

## 前提（運用）
- placeholder 生成は禁止（勝手に代替画像を作らない）
- サムネ/台本は削除しない（長期資産）
- 迷ったら勝手に判断しない（承認を取る）

## 実施
### 1) 全チャンネル監査（Mac Hot）
- 対象: `workspaces/thumbnails/projects.json` の `selected_variant_id` → `image_path`
- 実体: `workspaces/thumbnails/assets/<image_path>`
- placeholder 判定: 先頭ピクセルが `(20, 6, 6, 255)`（赤枠の典型値）

結果:
- `selected_total=779 / missing_total=1 / placeholder_total=0`
- missing は **CH06/191/1.png** のみ（label=`テスト`）

ログ:
- `workspaces/logs/ops/thumbs_audit/mac_hot_selected_audit__20260126T043559Z.json`
- `workspaces/logs/ops/thumbs_audit/summary__all_channels__20260126T044240Z.txt`

### 2) 追加調査（復元元の有無）
CH06/191/1.png は以下のいずれにも存在しないことを確認:
- Mac Hot: `workspaces/thumbnails/assets/CH06/191/1.png` → **不存在**
- Lenovo Vault: `C:\doraemon_share\ytm_workspaces\thumbnails\assets\CH06\191\1.png` → **不存在**
- Lenovo Archive: `C:\doraemon_share\archive\mac_assets\...\assets\CH06\191\1.png` → **不存在**

結論: **復元元が無い**ため、資産側の修復は不可。

### 3) 対応（ユーザー承認済み）
`workspaces/thumbnails/projects.json` から **CH06/191（テスト）エントリを削除**。
- 意図: 「存在しない実体」を selected として参照し続ける状態を解消し、
  ローカルUIの 404 要因を取り除く

### 4) 再監査（Mac Hot）
結果:
- `selected_total=778 / missing_total=0 / placeholder_total=0`

ログ:
- `workspaces/logs/ops/thumbs_audit/mac_hot_selected_audit__20260126T045943Z.json`
- `workspaces/logs/ops/thumbs_audit/summary__all_channels__20260126T045943Z.txt`

## Notion
- image-ddd に追記済み（全チャンネル監査→CH06/191削除で missing=0）


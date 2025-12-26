# REFERENCE_PATH_HARDCODE_INVENTORY — 直書き/旧名混入の監査入口（現行）

このファイルは「現行の監査入口（正本）」だけを保持し、巨大な棚卸しリストは置かない（探索ノイズ/誤誘導防止）。

## スナップショット（履歴）
- 2025-12-12 の棚卸しリスト（参照用・更新しない）:
  - `ssot/history/REFERENCE_PATH_HARDCODE_INVENTORY_20251212.md`

## 現行の正本（必ずこれを使う）
- Repo layout guard（root互換symlinkや旧alias再混入を検知）:
  - `python3 scripts/ops/repo_sanity_audit.py --verbose`
- 参照棚卸し（参照ゼロ判定/影響範囲確認）:
  - `python3 scripts/ops/repo_ref_audit.py --target <path-or-glob> --stdout`
- 直書きgrep（例）:
  - `rg --files-with-matches "/Users/dd/" apps packages scripts tests`
  - `rg --files-with-matches "workspaces/" apps packages scripts tests`

## 運用ループ（迷いどころゼロ化）
1) 参照ゼロ（コード参照ゼロ + フロー外）を機械棚卸し  
2) SSOT更新（まずSSOT）  
3) tracked は archive-first（`backups/graveyard/`）→削除/移動→ `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md` 記録  
4) `ssot_audit --strict` / `repo_sanity_audit` を通して再汚染を防止

# PLAN_REPO_DIRECTORY_REFACTOR — モノレポ整備（SSOT準拠 / symlink禁止）

## Plan metadata
- **Plan ID**: PLAN_REPO_DIRECTORY_REFACTOR
- **ステータス**: Active（symlink撤去 + 参照統一）
- **最終更新日**: 2025-12-25
- **旧版（詳細/当時のスナップショット）**: `ssot/history/PLAN_REPO_DIRECTORY_REFACTOR_legacy_20251218.md`（参照専用）

## 正本（必ずここを読む）
- ディレクトリ正本: `ssot/ops/OPS_REPO_DIRECTORY_SSOT.md`
- 現行フロー/SoT: `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`
- 生成物ライフサイクル: `ssot/plans/PLAN_OPS_ARTIFACT_LIFECYCLE.md`
- レガシー/ゴミ判定: `ssot/plans/PLAN_LEGACY_AND_TRASH_CLASSIFICATION.md`
- パスSSOT（コード）: `packages/factory_common/paths.py`

## ゴール（DoD）
- git tracked symlink（mode=120000）がゼロ
- ルート直下の別名ディレクトリがゼロ（互換symlink禁止）
- 実行コード/SSOT/Docs が `apps/` / `packages/` / `workspaces/` の正本パスのみを参照
- import は `PYTHONPATH=".:packages"`（または `sitecustomize.py`）で成立し、symlink に依存しない

## 実行順序（安全）
1. 参照の統一（Docs/コード/.gitignore）
2. archive-first（撤去前に退避）
3. tracked symlink 撤去（`git rm`）
4. audit/tests で再汚染を防止

## Stage 状態（要点）
- Stage 1: Path SSOT 導入（完了。詳細: `ssot/completed/PLAN_STAGE1_PATH_SSOT_MIGRATION.md`）
- Stage 2: `workspaces/` 正本化（完了）
- Stage 3: 旧資産/試作の隔離（完了: `backups/graveyard/` + `workspaces/_scratch/` に統一し、repoに `legacy/` ディレクトリは常駐させない）
- Stage 4: tracked symlink 撤去 + 参照統一（進行中）

## チェックリスト（2025-12-25）
- [x] root: `audio_tts_v2`, `script_pipeline`, `commentary_02_srt2images_timeline`, `factory_common`, `logs`, `progress`, `thumbnails`, `00_research`, `remotion`, `ui/*` を撤去
- [x] apps: `apps/remotion/input`, `apps/remotion/public/input` の撤去/置換
- [x] config: `configs/drive_oauth_client.json` の symlink 廃止（ローカル実ファイル運用へ）
- [x] ガード: tracked symlink と旧パス直書きを検出する audit を追加
- [x] 記録: `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md` に証跡追加

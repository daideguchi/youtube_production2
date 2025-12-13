# HISTORY_codex-memory — 変更履歴（運用ログ）

目的:
- 「いつ / 何を / なぜ」変えたかを SSOT として残し、運用やリファクタリングの判断を誤らないようにする。

運用ルール:
- 1 エントリ = 1 セッション（または 1 日）
- 変更対象（ファイル/機能）と理由、影響範囲を短く書く
- 実行ログ（build/test/run の出力）は `logs/regression/*` 等へ保存し、本履歴からリンクする

過去ログ:
- 旧履歴は `_old/ssot_old/history/HISTORY_codex-memory.md` に残っている（参照専用）。

---

## 2025-12-12
- SSOT の参照パスを `ssot/` 直下へ正規化し、確定フロー/確定 I/O/ログマップの正本を更新（`ssot/OPS_CONFIRMED_PIPELINE_FLOW.md`, `ssot/OPS_IO_SCHEMAS.md`, `ssot/OPS_LOGGING_MAP.md`）。
- 大規模リファクタ前提の計画書を更新（`ssot/PLAN_REPO_DIRECTORY_REFACTOR.md`, `ssot/PLAN_LEGACY_AND_TRASH_CLASSIFICATION.md`, `ssot/PLAN_OPS_ARTIFACT_LIFECYCLE.md`）。
- 確実ゴミの削除を実施し、復元可能な形で記録（`ssot/OPS_CLEANUP_EXECUTION_LOG.md`）。

## 2025-12-13
- Target 構成への“無破壊”前進として `packages/`/`workspaces/`/`legacy/` の scaffold と互換symlinkを整備（`packages/README.md`, `workspaces/README.md`, `factory_common/paths.py`）。
- Stage3 legacy隔離を実施し、トップレベルを現行フロー中心に整理（`legacy/*` へ移動 + 互換symlink。実行記録は `ssot/OPS_CLEANUP_EXECUTION_LOG.md`）。
- UI の storage アクセスを non-DOM ビルドでも落ちないように安全化（`apps/ui-frontend/src/utils/safeStorage.ts`, `apps/ui-frontend/src/utils/workspaceSelection.ts`）。
- 設計/進捗の下地を強化（`ssot/PLAN_REPO_DIRECTORY_REFACTOR.md` に進捗追記、`README.md` のディレクトリ概要更新、`tests/test_paths.py` を新レイアウトに追従）。
- 検証: `python3 -m pytest -q tests/test_paths.py` / `npm -C apps/ui-frontend run build`

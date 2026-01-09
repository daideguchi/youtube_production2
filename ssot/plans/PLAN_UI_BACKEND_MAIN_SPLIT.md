# PLAN_UI_BACKEND_MAIN_SPLIT — UI Backend `main.py` の肥大化を段階的に解消する（entrypoint維持）

## Plan metadata
- **Plan ID**: PLAN_UI_BACKEND_MAIN_SPLIT
- **ステータス**: Draft
- **担当/レビュー**: Owner: dd / Reviewer: dd
- **対象範囲 (In Scope)**: `apps/ui-backend/backend/main.py`, `apps/ui-backend/backend/routers/**`, `apps/ui-backend/backend/app/**`, `apps/ui-backend/backend/core/**`, `apps/ui-backend/backend/tests/**`
- **非対象 (Out of Scope)**: 挙動変更・API仕様変更・新機能追加（例外: “移設に伴う import/配置の最小変更” のみ）
- **関連 SoT/依存**:
  - `ssot/ops/OPS_UI_WIRING.md`
  - `ssot/ops/OPS_ENTRYPOINTS_INDEX.md`
  - `ssot/plans/PLAN_REPO_DIRECTORY_REFACTOR.md`
  - `packages/factory_common/paths.py`（パスSSOT）
- **最終更新日**: 2026-01-08

## 1. 背景と目的
- `apps/ui-backend/backend/main.py` が肥大化し、変更差分のレビュー/探索/衝突（並列運用）が起きやすい。
- 目的は「**entrypoint を維持したまま**、責務単位で分割して迷子を減らし、衝突半径を小さくする」こと。

## 2. 成果物と成功条件 (Definition of Done)
- `apps/ui-backend/backend/main.py` は **起動/設定/ルータ統合** に寄せ、ドメイン実装は別モジュールへ寄せる。
- 既存の API path / request / response の互換が保たれる（機能差分は出さない）。
- `python3 scripts/ops/pre_push_final_check.py --run-tests` が通る（最小ガード）。
- パス直書き禁止（`factory_common.paths` に寄せる）。

## 3. スコープ詳細
- **In Scope**
  - `main.py` から “純粋な移設が可能” なものを `routers/**` / `app/**` に移す。
  - `main.py` に残すのは次のみに制限: app生成、middleware、`include_router`、lifecycle/hook、最小の glue code。
  - 必要なら `apps/ui-backend/backend/app/` に “共通スキーマ/共通ユーティリティ” を追加する。
- **Out of Scope**
  - ルーティングSSOT/モデル管理の設計変更（別plan/decisionで扱う）
  - FastAPIの大規模アーキ変更（DI/コンテナ導入など）

## 4. 現状と課題の整理（観測）
- `main.py` は endpoint + 実装 + util + データアクセス が混在し、grep での導線が長い。
- 小さな変更でも差分が広がりやすく、衝突・レビュー負荷・“どこを触るべきか” の迷いが生じる。

## 5. 方針・設計概要（固定）
- **“移設は挙動を変えない” を最優先**し、段階的に薄くする（1PR=1ドメイン移設）。
- ルータは `apps/ui-backend/backend/routers/<domain>.py` に集約し、`main.py` は `include_router()` を行う。
- 共有の Pydantic モデル/レスポンス型は `apps/ui-backend/backend/app/` 側へ寄せる（循環 import を避ける）。
- “順序依存” があるもの（middleware、startup/shutdown、CORS 等）は `main.py` に残す。

## 6. 影響範囲と依存関係
- UI: `apps/ui-frontend/**`（API path 互換維持が必須）
- Ops/CLI: `scripts/start_all.sh`（backend 起動）
- SSOT: `ssot/ops/OPS_UI_WIRING.md`（主要導線の記述更新が必要になる場合あり）

## 7. マイルストーン / 実装ステップ
| ステージ | 具体タスク | オーナー | 期日 | ステータス |
| --- | --- | --- | --- | --- |
| 1 | `main.py` の “移設候補” をドメイン別に棚卸し（API群/共通util/スキーマ） | dd | - | Draft |
| 2 | 影響が小さい1ドメイン（例: `/api/llm-usage`）を router に移設し、`include_router` へ接続 | dd | - | Done |
| 2b | 影響が小さい1ドメイン（例: `/api/remotion/restart_preview`）を router に移設し、`include_router` へ接続 | dd | - | Done |
| 2c | 影響が小さい1ドメイン（例: `/api/workspaces/video/input/{run_id}/{asset_path}`）を router に移設し、`include_router` へ接続 | dd | - | Done |
| 3 | “共通スキーマ/共通util” の置き場を固定し、循環importを潰す（必要最小） | dd | - | Draft |
| 4 | 段階的に移設を繰り返し、`main.py` を起動/統合へ寄せる | dd | - | Draft |

## 8. TODO / チェックリスト
- [ ] 1回の移設で変える範囲を小さく保つ（差分が広がったら分割）
- [ ] ルーティング互換を確認（path, method, status_code, response_model）
- [ ] `scripts/ops/pre_push_final_check.py --run-tests` を通す
- [ ] 変更がSSOTの導線に影響する場合は先にSSOT更新

## 9. 決定ログ (ADR 簡易版)
- 2026-01-08: `main.py` は entrypoint を維持し、段階的に router/app/core へ分割していく（大規模移設は避ける）。

## 10. リスクと対策
- **リスク**: ルーティング登録順や依存が崩れて起動/挙動が変わる  
  **対策**: 1ドメインずつ移設 + pre-push check を必須化。
- **リスク**: 循環 import が出る  
  **対策**: スキーマ/共通utilの置き場を `app/` に寄せ、router は薄く保つ。

## 11. 非対応事項 / バックログ
- `video_production.py` の同様分割（別途計画化）
- API の整理（deprecated endpoints の整理/統合）

## 12. 参照リンク
- `apps/ui-backend/backend/main.py`
- `apps/ui-backend/backend/routers/`
- `ssot/ops/OPS_UI_WIRING.md`
- `ssot/plans/PLAN_REPO_DIRECTORY_REFACTOR.md`

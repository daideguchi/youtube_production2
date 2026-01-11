# UI Backend（FastAPI）構造メモ

目的: 「どこを直せば何が変わるか」を固定し、並列作業でも迷わないようにする。

## 入口（SSOT）
- エントリポイント: `apps/ui-backend/backend/main.py`（`backend.main:app`）
- 既存の router 群: `apps/ui-backend/backend/routers/*`（`main.py` から `include_router`）

## よく触る場所
- **モデル/実行スロット（正本）**: `configs/llm_router.yaml`, `configs/llm_task_overrides.yaml`, `configs/llm_model_slots.yaml`, `configs/llm_exec_slots.yaml`
- **UI 設定（キー/表示用）**: `configs/ui_settings.json`
  - 注意: ここは「APIキー/表示用」で、ルーティングSSOTではない。
- **台本量産の投入（UI→ops）**: `/api/batch-workflow/*`
  - `llm_model` は deprecated（数字だけなら slot 扱い）。推奨は `llm_slot` / `exec_slot`。

## テスト/検証
- Python import/syntax: `python3 -m compileall -q apps/ui-backend/backend`
- Backend tests: `python3 -m pytest -q apps/ui-backend/backend/tests`
- Pre-push 一括: `python3 scripts/ops/pre_push_final_check.py`

## 追加方針（迷子防止）
- 新しい API 群は原則 `routers/` に追加し、`main.py` は include_router を増やす。
- 既存の `main.py` は SSOT 参照が多いので、破壊的なリネーム/大移動は SSOT 更新とセットで段階導入する。


# Configs (root)
- LLM を含む共有設定の正本。新パイプライン（`packages/script_pipeline/`）もここを優先参照する。
- ランタイム固有の設定は `packages/script_pipeline/config` などサブディレクトリに置き、重複を避ける。
- 追加・変更時は `ssot/history/HISTORY_codex-memory.md` に記録する。

## LLM 関連の正本
- 正本（ルーティング）:
  - `configs/llm_router.yaml`（+ `configs/llm_router.local.yaml`）
  - `configs/llm_task_overrides.yaml`（+ `configs/llm_task_overrides.local.yaml`）
  - `configs/llm_model_codes.yaml`（+ `configs/llm_model_codes.local.yaml`）
  - `configs/llm_model_slots.yaml`（+ `configs/llm_model_slots.local.yaml`）
  - `configs/llm_exec_slots.yaml`（+ `configs/llm_exec_slots.local.yaml`）
- UI設定（キー/表示用。ルーティングSSOTではない）: `configs/ui_settings.json`
- OpenRouterメタ: `packages/script_pipeline/config/openrouter_models.json` を正とし、必要なら `python -m script_pipeline.tools.openrouter_models --free-only` で更新。

## 画像生成（ImageClient）
- 画像モデルSSOT: `configs/image_models.yaml`（providers/models/tiers/tasks）
- 任意の上書き（プロファイル）: `configs/image_task_overrides.yaml`
  - 選択: `IMAGE_CLIENT_PROFILE=<profile>`（default: `default`）
  - 例: `IMAGE_CLIENT_PROFILE=cheap_openrouter`（OpenRouter最安寄り）
- 任意のローカル上書き（gitignore）:
  - `configs/image_models.local.yaml`（SSOTを触らずにモデル/tiers/tasks を差し替え）
  - `configs/image_task_overrides.local.yaml`（overrideプロファイルのみ差し替え）
- 強制モデル（最優先）:
  - `IMAGE_CLIENT_FORCE_MODEL_KEY_<TASK>` または `IMAGE_CLIENT_FORCE_MODEL_KEY`

## 環境変数
- 正: リポジトリ直下の `.env` を唯一の正とする。
- 必須キーは `python3 scripts/check_env.py --env-file .env` で検証。
- Azure は任意（OpenRouterのみで運用する場合は未設定でも `./start.sh` は起動する）。
- `AZURE_OPENAI_API_VERSION` などバージョン系は実デプロイに合わせ、重複・古い値はコメントアウト。

## ヘルスチェック
- 環境: `python3 scripts/check_env.py --env-file .env`
- パイプライン進行（LLM含む）: `python3 -m script_pipeline.cli next --channel CH06 --video 033 --title "<title>"`（pending ステージを1つ進める）
- OpenRouterメタ更新: `python -m script_pipeline.tools.openrouter_models --free-only`

# Configs (root)
- LLM を含む共有設定の正本。新パイプライン (`script_pipeline/`) もここを優先参照する。
- ランタイム固有の設定は `script_pipeline/config` などサブディレクトリに置き、重複を避ける。
- 追加・変更時は `ssot/history/HISTORY_codex-memory.md` に記録する。

## LLM 関連の正本
- モデル仕様: `configs/llm_model_registry.yaml`
- フェーズ割当: `configs/llm_registry.json`（UI 上書きは `configs/ui_settings.json` 経由）
- OpenRouterメタ: `script_pipeline/config/openrouter_models.json` を正とし、必要なら `python -m script_pipeline.tools.openrouter_models --free-only` で更新。
- 旧 `commentary_01_srtfile_v2/configs/*` のレジストリは参考扱い（新パイプラインは参照しない）。

## 環境変数
- 正: `/Users/dd/10_YouTube_Automation/factory_commentary/.env` を唯一の正とする。
- 必須キーは `scripts/check_env.py --env-file /Users/dd/10_YouTube_Automation/factory_commentary/.env` で検証。
- Azure は任意（OpenRouterのみで運用する場合は未設定でも `./start.sh` は起動する）。
- `AZURE_OPENAI_API_VERSION` などバージョン系は実デプロイに合わせ、重複・古い値はコメントアウト。

## ヘルスチェック
- 環境: `python3 scripts/check_env.py --env-file /Users/dd/10_YouTube_Automation/factory_commentary/.env`
- パイプライン進行（LLM含む）: `python3 -m script_pipeline.cli next --channel CH06 --video 033 --title "<title>"`（pending ステージを1つ進める）
- OpenRouterメタ更新: `python -m script_pipeline.tools.openrouter_models --free-only`

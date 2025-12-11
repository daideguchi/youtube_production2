# OPS_ENV_VARS — 環境変数とキーの原則

## 原則
- 秘密鍵はリポジトリ直下の `.env` もしくはシェル環境変数に一元管理する。`.gemini_config` や `credentials/` 配下への複製は禁止。
- `.env.example` をベースに必要キーを埋める。既にシェルで export 済みの値があればそちらが優先される。

## 主な必須キー（抜粋）
- Gemini: `GEMINI_API_KEY`（画像/テキスト共通）
- OpenAI/OpenRouter: `OPENAI_API_KEY`
- Azure: `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`
- Drive/YouTube:  
  - `DRIVE_OAUTH_CLIENT_PATH`, `DRIVE_OAUTH_TOKEN_PATH`, `DRIVE_FOLDER_ID`  
  - `YT_OAUTH_CLIENT_PATH`, `YT_OAUTH_TOKEN_PATH`, `YT_PUBLISH_SHEET_ID`
- Factory Commentary (例): `DRIVE_UPLOAD_MODE=oauth`、必要に応じて `SCRIPT_PIPELINE_FALLBACK_MODEL` など。詳細はルート `README.md` を参照。
- E2Eスモーク実行フラグ（任意）: `RUN_E2E_SMOKE=1` をセットすると軽量スモーク（設定検証のみ）が走る。デフォルトでは実行されない。

## チェック方法
- `python3 commentary_02_srt2images_timeline/check_gemini_key.py` で GEMINI の設定確認（.env／環境変数のみを参照）。
- `env | grep -E \"GEMINI|OPENAI|AZURE_OPENAI\"` で export 状態を確認。
- `.env` の必須キー充足は `python3 scripts/check_env.py --env-file .env` で検証できる（空文字も不足として扱う）。
- LLMルーターのログ制御（任意）: `LLM_ROUTER_LOG_PATH`（デフォルト `logs/llm_usage.jsonl`）、`LLM_ROUTER_LOG_DISABLE=1` で出力停止。

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

## Agent-mode / THINK MODE（API LLM をエージェント運用へ置換）
Runbook/キュー運用の正本: `ssot/PLAN_AGENT_MODE_RUNBOOK_SYSTEM.md`, `ssot/agent_runbooks/README.md`

### 切替
- `LLM_MODE`:
  - `api`（デフォルト）: 通常どおり API LLM を呼ぶ
  - `agent`: LLM 呼び出しを止めて `logs/agent_tasks/` に pending を作る
  - `think`: `agent` の別名（THINK MODE）。フィルタ未指定なら **テキスト系だけ** を安全デフォルトで intercept

### キュー配置
- `LLM_AGENT_QUEUE_DIR`（任意）: 既定 `logs/agent_tasks`

### 対象タスクの絞り込み（任意）
- `LLM_AGENT_TASKS`（任意）: 例 `script_outline,tts_reading`（完全一致 allowlist）
- `LLM_AGENT_TASK_PREFIXES`（任意）: 例 `script_,tts_`（prefix allowlist）
- `LLM_AGENT_EXCLUDE_TASKS`（任意）: 例 `image_generation`（完全一致 blocklist）
- `LLM_AGENT_EXCLUDE_PREFIXES`（任意）: 例 `visual_`（prefix blocklist）

### Runbook マッピング
- `LLM_AGENT_RUNBOOKS_CONFIG`（任意）: 既定 `configs/agent_runbooks.yaml`

### 実行例
- THINK MODE（一発）:
  - `./scripts/think.sh --all-text -- python -m script_pipeline.cli run-all --channel CH06 --video 033`
- agent-mode（手動）:
  - `export LLM_MODE=agent`
  - `export LLM_AGENT_TASK_PREFIXES=script_`
  - `python -m script_pipeline.cli run-all --channel CH06 --video 033`

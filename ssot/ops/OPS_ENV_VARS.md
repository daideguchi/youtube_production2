# OPS_ENV_VARS — 環境変数とキーの原則

## 原則
- 秘密鍵はリポジトリ直下の `.env` もしくはシェル環境変数に一元管理する。`.gemini_config` や `credentials/` 配下への複製は禁止。
- `.env.example` をベースに必要キーを埋める。
  - 注: LLMRouter は `.env` を `override=False` で読み込むため、**シェル export / `./scripts/with_ytm_env.sh` の値が優先**される（未設定のみ `.env` で補完）。
- グローバルに `PYTHONPATH` を固定しない（特に旧リポジトリ配下を含むと、誤importで事故りやすい）。必要なら `./scripts/with_ytm_env.sh ...` を使う。
- **禁止（混乱の元）**: `GEMINI_MODEL` のような “モデル名直指定” env var を `.env` に置かない（本repoでは未使用/事故源）。
  - テキストLLMは `LLM_MODEL_SLOT`、画像は `channel_presets.json` / `templates.json` / `IMAGE_CLIENT_FORCE_MODEL_KEY_*`（必要時のみ）で制御する。
- **キーはチャット/Issue/ログに貼らない**（貼った時点で漏洩扱い）。誤って共有した場合は **即ローテ/無効化**し、`.env` を更新して `python3 scripts/check_env.py --env-file .env` で再検証する。

## Slack通知（任意）
目的:
- 長時間処理（script/audio/video/thumbnails 等）が終わった/止まった（THINK pending）タイミングで、Slackに通知して取りこぼしを防ぐ。

有効化:
- Webhook方式:
  - `YTM_SLACK_WEBHOOK_URL`（または `SLACK_WEBHOOK_URL`）に Incoming Webhook URL を設定する（git管理しない）。
- Bot方式（既存のSlack設定がこれの場合）:
  - `SLACK_BOT_TOKEN` と `SLACK_CHANNEL` を設定する（git管理しない）。

通知対象（任意）:
- `YTM_SLACK_NOTIFY_CMDS`（default: `script,audio,video,thumbnails,publish,resume,reconcile`）
  - `./ops` の top-level cmd をカンマ区切りで指定する（例: `video,thumbnails,resume`）
- `YTM_SLACK_NOTIFY_ALL=1` で全cmdを通知（スパム注意）

通知条件（任意）:
- `YTM_SLACK_NOTIFY_ON=both|success|failure`（default: `both`）
- `YTM_SLACK_NOTIFY_MIN_DURATION_SEC`（default: `0`）: この秒数未満の実行は通知しない（例: `30`）
- `YTM_SLACK_NOTIFY_AGENT_TASKS`（default: `1`）: THINK MODE の agent task（`./ops agent claim/complete`）も通知する（スパムが嫌なら `0`）

備考:
- `--llm think` で pending が出た場合は「失敗」ではなく `PENDING` として通知される（task埋め→再実行で続行）。
- `packages/script_pipeline/job_runner.py` の通知（`scripts/notifications.py`）も同じ Slack 設定を使う（Webhook/Bot 両対応）。
- `./ops` の “迷わない” レバー（任意）:
  - `./ops think <cmd> ...` / `./ops api <cmd> ...` / `./ops codex <cmd> ...` を使う（`--llm` の付け忘れを物理的に防ぐ）
  - `YTM_OPS_DEFAULT_LLM=think|api|codex` を設定すると、`./ops` で `--llm` を省略した時の既定を切り替えられる（例: 「今日は外部APIを使わない」→ `think`）
  - `YTM_OPS_TIPS=0` で `./ops` のヒント表示（stderr）を無効化できる（default: ON）
- モデル選択は **モデル名ではなく数字スロット**で行う（モデル名の書き換え禁止）。
  - `LLM_MODEL_SLOT`（default: `0`）: `configs/llm_model_slots.yaml` の `slots` から選ぶ
  - 個別調整は `configs/llm_model_slots.local.yaml`（git管理しない）で上書きする
  - スロット内で参照するモデルは **model code**（`open-k-1` / `fw-d-1` 等）に統一する（正本: `configs/llm_model_codes.yaml`）。
  - 推奨実行: `./scripts/with_ytm_env.sh --llm-slot 2 python3 ...`（または `./scripts/with_ytm_env.sh 2 python3 ...`）
  - スロット指定時は strict 扱いで、既定では先頭モデルのみ実行（失敗時は非`script_*`はTHINKへ）
  - 注:
    - `script_*` は THINK フォールバックしない（API停止時は即停止・記録）
    - `script_*` は **現運用では Fireworks（DeepSeek v3.2 exp + thinking）固定**（slot 0 の `script_tiers`）。切替は slot 定義（`configs/llm_model_slots.yaml` の `script_tiers` / `script_allow_openrouter`）で行う
      - Fireworks(text) を止めるのはデバッグ専用（`YTM_EMERGENCY_OVERRIDE=1 YTM_DISABLE_FIREWORKS_TEXT=1`）
- 実行モード選択は **exec slot** で行う（env直書きの増殖を防ぐ）。
  - `LLM_EXEC_SLOT`（default: `0`）: `configs/llm_exec_slots.yaml` の `slots` から選ぶ
  - 例:
    - `./scripts/with_ytm_env.sh --exec-slot 3 python3 ...`（THINK MODE: pendingを作る）
    - `./scripts/with_ytm_env.sh --exec-slot 1 python3 ...`（codex exec を強制ON）
    - `./scripts/with_ytm_env.sh x3 2 python3 ...`（shorthand: `xN`=exec slot, `N`=model slot）
  - 優先順位（互換/緊急用）:
    - `YTM_ROUTING_LOCKDOWN=0` または `YTM_EMERGENCY_OVERRIDE=1` のときだけ、明示の env（`LLM_MODE` / `YTM_CODEX_EXEC_*` / `LLM_API_FAILOVER_TO_THINK` 等）が勝つ
    - 通常運用（lockdown ON）では、これらの env は **検出した時点で停止**（ブレ防止）。slot を使う
    - slot は「安全なデフォルト」として適用される
- **運用ロック（ブレ防止）**: `YTM_ROUTING_LOCKDOWN=1`（default: ON）
  - 目的: どのAIエージェントが実行しても「同じスロット/コードなら同じルーティング」になるように、**上書き経路を潰す**
  - ON のとき、次の “非スロット上書き” は **検出した時点で停止**（事故防止）:
    - モデル系: `LLM_FORCE_MODELS` / `LLM_FORCE_MODEL`（※数字だけならslot互換として許可） / `LLM_FORCE_TASK_MODELS_JSON`
    - 実行系: `LLM_MODE` / `LLM_API_FAILOVER_TO_THINK` / `LLM_API_FALLBACK_TO_THINK` / `YTM_CODEX_EXEC_*`
    - 隠し切替（禁止）: `YTM_SCRIPT_ALLOW_OPENROUTER` / `LLM_ENABLE_TIER_CANDIDATES_OVERRIDE`
  - SSOT保護: `configs/llm_task_overrides.yaml` は運用中に書き換えない（モデル名の書き換え事故防止）
    - ロックダウンONでは「未コミット差分がある」だけで停止する（`scripts/with_ytm_env.sh` / 主要entrypointで検知）
    - 禁止モデル（例）: `az-gpt5-mini-1` / `azure_gpt5_mini` は task overrides 経由での利用を **完全禁止**（fallbackでも不可）
  - 代わりに使うレバー:
    - どのモデル系統で回すか → `LLM_MODEL_SLOT`（数字）
    - どこで動かすか → `LLM_EXEC_SLOT`（数字）
    - 画像 → `g-1` / `f-1` / `f-3` / `f-4`（`configs/image_model_slots.yaml`）
  - 緊急デバッグだけ例外: `YTM_EMERGENCY_OVERRIDE=1`（この実行だけロック解除。通常運用では使わない）
- 画像モデル選択は **短いslot code**（例: `g-1`, `f-4`）で行う（`image_models.yaml` の書き換え禁止）。
  - スロット定義: `configs/image_model_slots.yaml`（個別調整: `configs/image_model_slots.local.yaml`）
  - 適用先:
    - 動画内画像: `packages/video_pipeline/config/channel_presets.json` の `channels.<CH>.image_generation.model_key`
    - サムネ: `workspaces/thumbnails/templates.json` の `templates[].image_model_key`
    - incident/debug（その実行だけ）: `IMAGE_CLIENT_FORCE_MODEL_KEY_<TASK>=f-1`（例: `IMAGE_CLIENT_FORCE_MODEL_KEY_VISUAL_IMAGE_GEN=f-1`）
      - 注意: `.env` に `IMAGE_CLIENT_FORCE_MODEL_KEY*` を恒久セットする運用は禁止（ロックダウンONで停止）。「その実行だけ」prefixで明示する。
  - **禁止（動画内画像）**: `visual_image_gen`（動画内画像）では Gemini 3 系の画像モデルは使わない（例: `gemini_3_pro_image_preview`, `openrouter_gemini_3_pro_image_preview`）。
    - `IMAGE_CLIENT_FORCE_MODEL_KEY_VISUAL_IMAGE_GEN` / `IMAGE_CLIENT_FORCE_MODEL_KEY_IMAGE_GENERATION` / `IMAGE_CLIENT_FORCE_MODEL_KEY` に `gemini-3` / `gemini_3` を含む値を入れた時点で停止する（ガードあり）。
  - **許可（サムネ）**: `thumbnail_image_gen`（サムネ背景生成）は Gemini 3 系を使っても良い（必要時のみ明示して使う）。
    - 例: `IMAGE_CLIENT_FORCE_MODEL_KEY_THUMBNAIL_IMAGE_GEN=gemini_3_pro_image_preview ...`
  - 任意: `IMAGE_CLIENT_MODEL_SLOTS_PATH` で slot 定義ファイルを差し替えできる（検証/一時切替用途）

## 主な必須キー（抜粋）
- Gemini: `GEMINI_API_KEY`（画像/テキスト共通）
- Fireworks（画像）: `FIREWORKS_IMAGE`（画像生成 / 推奨。互換: `FIREWORKS_API_KEY`）
  - キーローテ（任意・推奨）:
    - `FIREWORKS_IMAGE_KEYS_FILE`（任意）: 複数キーを1行1キーで列挙したファイルパス（コメント `#` 可）。
      - 既定探索: `~/.ytm/secrets/fireworks_image_keys.txt`（`YTM_SECRETS_ROOT` でルート変更可）
      - 追加/整形: `python3 scripts/ops/fireworks_keyring.py --pool image add --key ...`（キーは表示しない）
    - `FIREWORKS_IMAGE_KEYS`（任意）: 追加キーをカンマ区切りで列挙（例: `key1,key2,...`）。
    - `FIREWORKS_IMAGE_KEYS_STATE_FILE`（任意）: キー状態（exhausted/invalid等）を保存するファイルパス。
      - 既定: `~/.ytm/secrets/fireworks_image_keys_state.json`
      - 更新: `python3 scripts/ops/fireworks_keyring.py --pool image check --show-masked`（既定: `GET /inference/v1/models`。トークン消費なし。`412` は `suspended`）
- Fireworks（台本/本文）: `FIREWORKS_SCRIPT`（文章執筆 / LLMRouter provider=fireworks 用。互換: `FIREWORKS_SCRIPT_API_KEY`）
  - キーローテ（任意・推奨）:
    - `FIREWORKS_SCRIPT_KEYS_FILE`（任意）: 複数キーを1行1キーで列挙したファイルパス（コメント `#` 可）。
      - 既定探索: `~/.ytm/secrets/fireworks_script_keys.txt`（`YTM_SECRETS_ROOT` でルート変更可）
      - 追加/整形: `python3 scripts/ops/fireworks_keyring.py --pool script add --key ...`（キーは表示しない）
    - `FIREWORKS_SCRIPT_KEYS`（任意）: 追加キーをカンマ区切りで列挙（例: `key1,key2,...`）。
    - `FIREWORKS_SCRIPT_KEYS_STATE_FILE`（任意）: キー状態（exhausted/invalid等）を保存するファイルパス。
      - 既定: `~/.ytm/secrets/fireworks_script_keys_state.json`
      - 更新（既定）: `python3 scripts/ops/fireworks_keyring.py --pool script check --show-masked`（`GET /inference/v1/models`。トークン消費なし）
      - 更新（推論で確認）: `python3 scripts/ops/fireworks_keyring.py --pool script check --mode chat --show-masked`（`POST /chat/completions`。最小トークン消費）
      - 退避（使えないキーを隔離）: `python3 scripts/ops/fireworks_keyring.py --pool script quarantine --show-masked`（既定: `~/.ytm/secrets/fireworks_script_keys.quarantine.txt`）
      - 復帰（隔離から戻す）: `python3 scripts/ops/fireworks_keyring.py --pool script restore --show-masked`
    - `FIREWORKS_SCRIPT_KEYS_SKIP_EXHAUSTED`（default: `1`）: stateで `exhausted/invalid/suspended` のキーをローテ候補から除外する。
    - Fireworks コストガード（任意・強く推奨）:
      - `FIREWORKS_BUDGET_MAX_CALLS_PER_ROUTING_KEY` / `FIREWORKS_BUDGET_MAX_TOKENS_PER_ROUTING_KEY`: 1本（`LLM_ROUTING_KEY=CHxx-NNN`）あたりの上限
      - `FIREWORKS_BUDGET_MAX_CALLS_TOTAL` / `FIREWORKS_BUDGET_MAX_TOKENS_TOTAL`: プロセス全体の上限
      - いずれかを設定すると、上限到達後は Fireworks を **停止**（他プロバイダへ自動フォールバックしない）
    - 動作: まず `FIREWORKS_SCRIPT` を使い、Fireworks が `401/402/403/412` 等で失敗したら **同一プロバイダ内で** 次キーへ切替して再試行する。
      - それでも全滅した場合は停止（`script_*` は THINK フォールバックしない。runbookに従って復旧）。
  - 並列運用（固定）: **同一キーの同時利用は禁止**（エージェント間排他）
    - lease dir: `~/.ytm/secrets/fireworks_key_leases/`（override: `FIREWORKS_KEYS_LEASE_DIR`）
    - TTL: `FIREWORKS_SCRIPT_KEY_LEASE_TTL_SEC` / `FIREWORKS_IMAGE_KEY_LEASE_TTL_SEC`（default: `1800`）
    - 画像は `FIREWORKS_IMAGE_KEY_MAX_ATTEMPTS`（default: `5`）回まで別キーへ切替して再試行する
    - lease は sha256 fingerprint で管理し、**キー本文は保存しない**
- OpenRouter: `OPENROUTER_API_KEY`（LLMRouter provider=openrouter 用）
- OpenAI（任意）: `OPENAI_API_KEY`（openai provider を使う場合のみ）
- Azure（任意）: `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`（Azureを使う場合のみ。未設定でも `./start.sh` は起動する）
- Drive/YouTube:  
  - `DRIVE_OAUTH_CLIENT_PATH`, `DRIVE_OAUTH_TOKEN_PATH`, `DRIVE_FOLDER_ID`  
  - `YT_OAUTH_CLIENT_PATH`, `YT_OAUTH_TOKEN_PATH`, `YT_PUBLISH_SHEET_ID`
- Factory Commentary (例): `DRIVE_UPLOAD_MODE=oauth`、必要に応じて `SCRIPT_PIPELINE_FALLBACK_MODEL` など。詳細はルート `README.md` を参照。
- E2Eスモーク実行フラグ（任意）: `RUN_E2E_SMOKE=1` をセットすると軽量スモーク（設定検証のみ）が走る。デフォルトでは実行されない。

## チェック方法
- `python3 packages/video_pipeline/check_gemini_key.py` で GEMINI の設定確認（.env／環境変数のみを参照）。
- `env | grep -E \"GEMINI|OPENAI|AZURE_OPENAI\"` で export 状態を確認。
- `.env` の必須キー充足は `python3 scripts/check_env.py --env-file .env` で検証できる（空文字も不足として扱う）。
- LLMルーターのログ制御（任意）: `LLM_ROUTER_LOG_PATH`（デフォルト `workspaces/logs/llm_usage.jsonl`）、`LLM_ROUTER_LOG_DISABLE=1` で出力停止。
  - `llm_usage.jsonl` には `routing_key`（例: `CH10-010`）が記録されるため、1本あたりの呼び出し回数/トークン量を後追いできる。
  - 例: `python3 scripts/ops/llm_usage_report.py --channel CH10 --video 010 --task-prefix script_`
- TTS（任意）: `YTM_TTS_KEEP_CHUNKS=1` をセットすると、TTS成功後も `workspaces/audio/final/**/chunks/` を残す（デフォルトは削除）。

## Script pipeline: Web Search（topic_research の検索/ファクトチェック）
`packages/script_pipeline/runner.py` の `topic_research` で利用され、`content/analysis/research/search_results.json` に保存される。

### チャンネル別ポリシー（SoT）
検索を「毎回やる/やらない」を固定すると、チャンネルによっては **コスト増・内容汚染** の原因になる。  
そのため、チャンネル別に `configs/sources.yaml` で実行可否を決める。

- `configs/sources.yaml: channels.CHxx.web_search_policy`（default: `auto`）
  - `disabled`: 検索を実行しない（`search_results.json` は `provider=disabled, hits=[]` を必ず書く）
  - `auto`: 通常どおり検索を試みる（provider は下記 `YTM_WEB_SEARCH_PROVIDER` に従う）
  - `required`: 検索を必ず試みる（provider は下記に従う。失敗してもパイプライン自体は止めないが、`status.json` に記録される）

- `YTM_WEB_SEARCH_PROVIDER`（default: `auto`）
  - 運用既定: `./scripts/with_ytm_env.sh` は **未設定なら `disabled` を自動セット**（LLMベース検索のコスト/汚染を避けるため）
  - 必要なときだけ上書き: `YTM_WEB_SEARCH_PROVIDER=brave ./scripts/with_ytm_env.sh ...`
  - 値:
  - `auto`: `BRAVE_SEARCH_API_KEY` があれば Brave、無ければ `OPENROUTER_API_KEY` で OpenRouter 検索モデル
  - `brave`: Brave Search API を使用
  - `openrouter`: OpenRouter 検索モデルを使用（default model は下記）
  - `disabled`: 検索を実行しない（`hits=[]` の JSON を書く）
- `BRAVE_SEARCH_API_KEY`（任意）: Brave Search API（provider=brave/auto のとき使用）
- `YTM_WEB_SEARCH_OPENROUTER_TASK`（default: `web_search_openrouter`）: provider=openrouter/auto のときに使う LLMRouter task key（`configs/llm_router.yaml: tasks`）
- `YTM_WEB_SEARCH_OPENROUTER_MODEL`（任意）: OpenRouter検索モデルの上書き（LLMRouter が `model_name` / `deployment` / `provider:model_id` 形式で解決できるもの）
  - 例: `perplexity/sonar`
  - 例: `openrouter:perplexity/sonar`
  - 使うモデルは `configs/llm_router.yaml: models` に登録しておく（未登録の場合は既定タスク設定にフォールバック）
- `YTM_WEB_SEARCH_COUNT`（default: `8`）: 検索結果の最大件数
- `YTM_WEB_SEARCH_TIMEOUT_S`（default: `20`）: 検索リクエストの timeout（秒）
- `YTM_WEB_SEARCH_FORCE`（default: `0`）: `1` で既存の `search_results.json` があっても再検索
  - 注: `provider=disabled` の既存ファイルも、`force=0` なら再生成しない（入力の安定化 / artifact差分停止の回避）

## Script pipeline: Wikipedia（topic_research の補助ソース）
`packages/script_pipeline/runner.py` の `topic_research` で利用され、`content/analysis/research/wikipedia_summary.json` に保存される。

### チャンネル別ポリシー（SoT）
Wikipedia を「毎回使う/使わない」を固定すると、チャンネルによっては **内容汚染** の原因になる。  
そのため、チャンネル別に `configs/sources.yaml` で実行可否を決める（未設定時は web_search_policy から既定を導出）。

- `configs/sources.yaml: channels.CHxx.wikipedia.policy`（default: `auto`）
  - `disabled`: Wikipedia を参照しない（`wikipedia_summary.json` は `provider=disabled` を必ず書く）
  - `auto`: 通常どおり参照を試みる（見つからない/失敗してもパイプラインは止めない）
  - `required`: 参照を必ず試みる（失敗してもパイプラインは止めないが、`status.json` に記録される）
- `configs/sources.yaml: channels.CHxx.wikipedia.lang`（default: `ja`）: まず探すWikipedia言語
- `configs/sources.yaml: channels.CHxx.wikipedia.fallback_lang`（default: `en`）: 見つからない場合のフォールバック言語（空なら無効）

### 環境変数（任意）
- `YTM_WIKIPEDIA_FORCE`（default: `0`）: `1` で既存の `wikipedia_summary.json` があっても再取得
- `YTM_WIKIPEDIA_LANG`（default: `ja`）: 参照言語の上書き
- `YTM_WIKIPEDIA_FALLBACK_LANG`（default: `en`）: フォールバック言語の上書き
- `YTM_WIKIPEDIA_TIMEOUT_S`（default: `20`）: Wikipedia API の timeout（秒）

## Script pipeline: Master Plan（設計図 / 高コスト推論はここで1回だけ）
`packages/script_pipeline/runner.py` の `script_master_plan` に適用される。

目的:
- 長尺で起きる「迷子/脱線/終盤の崩壊」を **章執筆前** に抑える。
- 高コストモデル（例: Claude Opus 4.5）を使う場合は **この工程で1回だけ**（本文を書かせない）。

出力:
- `workspaces/scripts/{CH}/{NNN}/content/analysis/master_plan.json`

有効化（デフォルトOFF）:
- `SCRIPT_MASTER_PLAN_LLM`（default: `0`）: `1` で LLM による設計図サマリ生成を試す（失敗しても自動で決定論に戻して続行）。
- `SCRIPT_MASTER_PLAN_LLM_TASK`（default: 空）: 使う task key（`configs/llm_router.yaml: tasks`）。例: `script_master_plan_opus`（Opus専用に作る）。
- `SCRIPT_MASTER_PLAN_LLM_CHANNELS`（default: 空）: 実行を許可するチャンネルの allowlist（例: `CH10`）。`all` または `*` で全チャンネル（非推奨）。

コスト暴走防止（強制推奨）:
- `SCRIPT_MASTER_PLAN_LLM_STRICT_SINGLE_MODEL`（default: `1`）:
  - `1`: task の tier が **1モデルのみ**のときだけ実行。複数候補や `LLM_FORCE_MODELS` で複数指定がある場合は自動スキップ。
  - `0`: 上記制限を外す（非推奨）。

微調整（任意）:
- `SCRIPT_MASTER_PLAN_LLM_MAX_TOKENS`（default: `1200`）
- `SCRIPT_MASTER_PLAN_LLM_TEMPERATURE`（default: `0.2`）

観測（必ず残る）:
- 実行結果: `workspaces/scripts/{CH}/{NNN}/status.json: stages.script_master_plan.details`

## Script runbook: seed-expand（Seed→Expand / Seed生成は原則低コスト）
`scripts/ops/script_runbook.py seed-expand` に適用される。

目的:
- 長文を一撃で書かせず、短いSeed→追記（`script_validation` の Extend/Expand）で収束させる。
- Seed生成は **原則低コスト**で1回だけ（リトライでコストを増やさない）。

## Script pipeline: Aテキスト品質ゲート（任意）
`packages/script_pipeline/runner.py` の `script_validation` に適用される。

- `SCRIPT_VALIDATION_LLM_QUALITY_GATE`（default: `1`）: LLM品質ゲート（Judge→Fixer→必要ならExtend）を有効化。無効化は `0`。
- `SCRIPT_VALIDATION_LLM_MAX_ROUNDS`（default: `3`）: Judge→Fixer の最大反復回数（v2は既定3。fail→Fix→Judge→Fix→Judge で収束させる）。コスト優先なら `2` に下げる。
- `SCRIPT_VALIDATION_LLM_HARD_FIX_MAX`（default: `2`）: Fixer出力がハード禁則（字数/見出し/箇条書き等）に違反した場合の追加修正回数。
- `SCRIPT_VALIDATION_LLM_MAX_A_TEXT_CHARS`（default: `30000`）: Aテキストがこの文字数（spoken chars）を超える場合、全文LLMゲートを自動スキップ（機械チェックは実行）。`0` で無効化。
- `SCRIPT_VALIDATION_QUALITY_JUDGE_TASK`（default: `script_a_text_quality_judge`）: LLMルーターの task key。
- `SCRIPT_VALIDATION_QUALITY_FIX_TASK`（default: `script_a_text_quality_fix`）: LLMルーターの task key。
- `SCRIPT_VALIDATION_QUALITY_EXTEND_TASK`（default: `script_a_text_quality_extend`）: 字数不足のみを「追記専用」で救済する task key。
- `SCRIPT_VALIDATION_AUTO_LENGTH_FIX`（default: `0`）: ハードNGが **`length_too_long` のみ**のとき、緊急縮小（shrink）で救済する（危険なので既定OFF）。
- `SCRIPT_VALIDATION_LLM_REBUILD_ON_FAIL`（default: `0`）: Fixerで収束しない場合に、最終手段として「設計→再執筆（Rebuild）」を試す。無効化は `0`（既定OFF）。
- `SCRIPT_VALIDATION_QUALITY_REBUILD_PLAN_TASK`（default: `script_a_text_rebuild_plan`）: Rebuildの「設計図（JSON）生成」task key。
- `SCRIPT_VALIDATION_QUALITY_REBUILD_DRAFT_TASK`（default: `script_a_text_rebuild_draft`）: Rebuildの「本文生成」task key。

注:
- task key の実体（tier/model/options）は `configs/llm_router.yaml` と `configs/llm_task_overrides.yaml` を正とする。
- `SCRIPT_PIPELINE_DRY=1` のときは品質ゲートを走らせない（dry-run）。

## Script pipeline: 最終ポリッシュ（全文の自然化 / script_validation）
`packages/script_pipeline/runner.py` の `script_validation` の終盤に適用される。

- `SCRIPT_VALIDATION_FINAL_POLISH`（default: `auto`）: `auto|0|1`
  - `auto`: 長尺（min chars以上）または品質ゲートが介入した場合のみ、最大1回の全文ポリッシュを行う
  - `0`: 実行しない
  - `1`: 常に実行する（最大1回）
- `SCRIPT_VALIDATION_FINAL_POLISH_TASK`（default: `script_a_text_final_polish`）: LLMルーターの task key
- `SCRIPT_VALIDATION_FINAL_POLISH_MIN_CHARS`（default: `12000`）: `auto` のときの最小文字数（min chars判定）
- `SCRIPT_VALIDATION_FORCE_FINAL_POLISH_FOR_CODEX_DRAFT`（default: `1`）:
  - 原則: Codex exec は Aテキスト本文 task を実行しない（SoT: `configs/codex_exec.yaml`）。そのため通常は影響しない。
  - `1`: （例外運用で）章草稿が `codex_exec` 由来だった場合、最終本文の言い回し混入を避けるため **強制で全文ポリッシュ** を実行する
  - `0`: 強制しない（`SCRIPT_VALIDATION_FINAL_POLISH` の判定に従う）

## Script pipeline: 意味整合（Semantic alignment gate）
`packages/script_pipeline/runner.py` の `script_outline`（事前）と `script_validation`（最終）に適用される。

- `SCRIPT_OUTLINE_SEMANTIC_ALIGNMENT_GATE`（default: `1`）: アウトライン段階の事前意味整合ゲートを有効化（章草稿=高コストの前に逸脱を止める）。
- `SCRIPT_VALIDATION_SEMANTIC_ALIGNMENT_GATE`（default: `1`）: `script_validation` の意味整合ゲートを有効化。
- `SCRIPT_VALIDATION_SEMANTIC_ALIGNMENT_REQUIRE_OK`（default: `0`）: 合格条件を制御。
  - `0`: `verdict: major` のみ停止（ok/minor は合格; 量産デフォルト）
  - `1`: `verdict: ok` 以外は停止（minor/major は停止; より厳密にブロック）
- `SCRIPT_VALIDATION_SEMANTIC_ALIGNMENT_AUTO_FIX`（deprecated/ignored）: `script_validation` 内で本文を自動書き換えない（事故防止）。修正は `python3 -m script_pipeline.cli semantic-align --apply` を **手動**で実行する。
- `SCRIPT_VALIDATION_SEMANTIC_ALIGNMENT_AUTO_FIX_MINOR`（deprecated/ignored）: 同上（自動書き換えはしない）。
- `SCRIPT_VALIDATION_SEMANTIC_ALIGNMENT_AUTO_FIX_MAJOR`（deprecated/ignored）: 同上（自動書き換えはしない）。
- `SCRIPT_VALIDATION_SEMANTIC_ALIGNMENT_MAX_FIX_ATTEMPTS`（deprecated/ignored）: 同上（自動書き換えはしない）。
- `SCRIPT_SEMANTIC_ALIGNMENT_MAX_A_TEXT_CHARS`（default: `30000`）: 判定に渡す最大文字数（超過時は先頭+末尾抜粋で判定し、auto-fix は安全のためスキップ）。

## Script pipeline: Fact check（完成台本 / script_validation）
`packages/script_pipeline/runner.py` の `script_validation` の終盤で実行され、`content/analysis/research/fact_check_report.json` に保存される。

### チャンネル別ポリシー（SoT）
- `configs/sources.yaml: channels.CHxx.fact_check_policy`（任意。未設定時は `web_search_policy` から既定を導出）
  - `disabled`: 実行しない（reportは `verdict=skipped` を必ず書く）
  - `auto`: `fail` のときのみ停止（`warn` は通すがreportは残る）
  - `required`: `pass` 以外は停止（`warn/fail` で止める）

### 環境変数（任意）
- `YTM_FACT_CHECK_POLICY`（override）: `disabled|auto|required`
- `YTM_FACT_CHECK_MAX_CLAIMS`（default: `12`）: 抽出するclaim上限
- `YTM_FACT_CHECK_MIN_CLAIM_SCORE`（default: `4`）: claim抽出の最小スコア（客観要素が弱い文を除外）
- `YTM_FACT_CHECK_MAX_URLS`（default: `8`）: 参照URL上限
- `YTM_FACT_CHECK_MAX_SOURCES_PER_CLAIM`（default: `2`）: claimごとに渡す抜粋の上限
- `YTM_FACT_CHECK_EXCERPT_MAX_CHARS`（default: `1400`）: 抜粋の最大長
- `YTM_FACT_CHECK_FETCH_TIMEOUT_S`（default: `20`）: URL本文取得timeout
- `YTM_FACT_CHECK_FETCH_MAX_CHARS`（default: `20000`）: URL本文の最大文字数
- `YTM_FACT_CHECK_CODEX_TIMEOUT_S`（default: `180`）: `codex exec` のtimeout
- `YTM_FACT_CHECK_CODEX_MODEL`（任意）: codex exec に渡すモデル名
- `YTM_FACT_CHECK_FORCE`（default: `0`）: `1` で fingerprint 一致でも再実行
- `YTM_FACT_CHECK_LLM_FALLBACK`（default: `1`）: Codex失敗時に API（LLMRouter）へフォールバック
- `YTM_FACT_CHECK_LLM_TASK`（default: `script_a_text_quality_judge`）: フォールバックで使う LLMRouter task key
- `YTM_FACT_CHECK_LLM_TIMEOUT_S`（default: `120`）: フォールバックのtimeout
- `YTM_FACT_CHECK_LLM_MAX_TOKENS`（default: `2000`）: フォールバックのmax tokens

## Codex exec layer（非対話）: Codex優先 → APIフォールバック
`packages/factory_common/llm_router.py` が、選択された task に対して `codex exec --sandbox read-only` を先に試し、失敗時は既存の LLM API（OpenRouter/Azure 等）へフォールバックする。

重要（固定ルール）:
- Codex管理シェル（`CODEX_MANAGED_BY_NPM=1`）では、`configs/codex_exec.yaml:auto_enable_when_codex_managed=true` のとき **自動で有効**になる（未設定時の既定挙動）。
- **境界（固定）**: Aテキスト本文（`chapters/*.md` / `assembled*.md`）を書き換える可能性がある task は **Codex exec に回さない**。
  - 対象（例）: `script_chapter_draft`, `script_cta`, `script_format`, `script_chapter_review`, `script_a_text_*`, `script_semantic_alignment_fix`
  - 理由: Codex の言い回しが本文へ混入する事故を構造的に防ぐ（本文は常に LLM API 側で統一）
  - SoT: `configs/codex_exec.yaml: selection.exclude_tasks`
- それ以外の非本文タスク（`script_outline`/`script_topic_research`/各種 JSON 生成/判定など）は Codex exec 優先（失敗時は LLMRouter API へフォールバック）。
  - 例外（固定）: `image_generation`, `web_search_openrouter` は Codex exec 対象外（既定でも除外）。
  - 詳細SSOT: `ssot/ops/OPS_SCRIPT_GENERATION_ARCHITECTURE.md`（2.4）

### 設定（SoT）
- `configs/codex_exec.yaml`（tracked）
- 任意のローカル上書き: `configs/codex_exec.local.yaml`

### 環境変数（任意）
- 注意: 通常運用では `YTM_ROUTING_LOCKDOWN=1` のため、`YTM_CODEX_EXEC_*` の直接上書きは **検出した時点で停止**する。切替は `LLM_EXEC_SLOT=1/2` と `configs/codex_exec*.yaml` を使う（緊急デバッグのみ `YTM_EMERGENCY_OVERRIDE=1`）。
- `YTM_CODEX_EXEC_ENABLED`（override）: `1` で強制ON / `0` で強制OFF（未設定なら `configs/codex_exec.yaml` と `CODEX_MANAGED_BY_NPM` に従う）
- `YTM_CODEX_EXEC_DISABLE`（default: `0`）: `1` で強制OFF（緊急停止用）
- `YTM_CODEX_EXEC_ENABLE_IN_PYTEST`（default: `0`）: `1` のときだけ pytest 中の Codex exec を許可（既定はテスト安定のためOFF）
- `YTM_CODEX_EXEC_PROFILE`（default: `claude-code`）: `codex exec --profile` に渡すプロファイル名
- `YTM_CODEX_EXEC_MODEL`（任意）: `codex exec -m` に渡すモデル名
- `YTM_CODEX_EXEC_TIMEOUT_S`（default: `180`）: `codex exec` のtimeout（秒）
- `YTM_CODEX_EXEC_SANDBOX`（default: `read-only`）: `codex exec --sandbox`（運用では read-only 固定推奨）
- `YTM_CODEX_EXEC_EXCLUDE_TASKS`（任意）: `codex exec` を **試さない task** をカンマ区切りで指定（例: `script_outline,script_topic_research`）
- `YTM_SCRIPT_ALLOW_OPENROUTER`（legacy）: 旧運用互換。`YTM_ROUTING_LOCKDOWN=1`（既定）では **検出した時点で停止**するため、通常運用では使わない（slot定義で固定する）

## Script pipeline: Planning整合（内容汚染の安全弁）
- `SCRIPT_BLOCK_ON_PLANNING_TAG_MISMATCH`（default: `0`）: Planning 行が `tag_mismatch` の場合に高コスト工程の前で停止する（strict運用）。既定は停止せず、汚染されやすいテーマヒントだけ落として続行する（タイトルは常に正）。

## Script pipeline: エピソード重複（採用済み回と被せない）
- `SCRIPT_BLOCK_ON_EPISODE_DUPLICATION`（default: `0`）: 採用済み（Planning CSV の `進捗=投稿済み/公開済み` または `published_lock=true`（UI の `投稿完了`））の回と `キーコンセプト` が重複する場合、`topic_research/script_outline/script_draft` 等の高コスト工程の前で停止する（strict運用）。既定は停止せず、lint警告のみ。

## Script pipeline: リサーチ足場（URLソース）不足で停止（任意 / strict）
- `SCRIPT_BLOCK_ON_MISSING_RESEARCH_SOURCES`（default: `0`）: `topic_research` 実行前に **検証用URL（search_hits/references/wiki）が0件** の場合に停止する（strict運用）。
  - 既定は停止しない（= Web検索/Wikipedia が空でもパイプラインは続行する）。
  - 停止時は `status.json: stages.topic_research.details.fix_hints` に、Braveの有効化または手動投入（research bundle）の案内が出る。
  - 手動投入の手順（SoT）: `ssot/ops/OPS_RESEARCH_BUNDLE.md`

## Agent-mode / THINK MODE（API LLM をエージェント運用へ置換）
Runbook/キュー運用の正本: `ssot/plans/PLAN_AGENT_MODE_RUNBOOK_SYSTEM.md`, `ssot/agent_runbooks/README.md`

### 切替（推奨: exec-slot）
- `LLM_EXEC_SLOT`:
  - `0`（デフォルト）: 通常どおり API LLM を呼ぶ
  - `3`（THINK MODE）: LLM 呼び出しを止めて `workspaces/logs/agent_tasks/` に pending を作る（安全デフォルトで intercept）
  - `4`（AGENT MODE）: LLM 呼び出しを止めて `workspaces/logs/agent_tasks/` に pending を作る（明示）
- 互換/緊急用: `LLM_MODE=api|agent|think`（通常運用のロックダウンONでは停止。使うなら `YTM_EMERGENCY_OVERRIDE=1`）

### キュー配置
- `LLM_AGENT_QUEUE_DIR`（任意）: 既定 `workspaces/logs/agent_tasks`

### 担当エージェント名（推奨 / agent_org write系は必須）
- `LLM_AGENT_NAME`（推奨 / `scripts/agent_org.py` の lock/memo/board 等の write 操作では **必須**）: 例 `LLM_AGENT_NAME=Mike`
  - pending 生成時に `claimed_by` / `claimed_at` を自動付与（担当者の見える化）
  - `python scripts/agent_runner.py complete ...` 実行時に results の `completed_by` に保存
- `AGENT_NAME`（任意）: 互換用の別名（`LLM_AGENT_NAME` が未指定のときのみ参照）
- CLIでの上書き: `python scripts/agent_runner.py --agent-name Mike claim <TASK_ID>`

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
  - `export LLM_EXEC_SLOT=4`
  - `export LLM_AGENT_TASK_PREFIXES=script_`
  - `python -m script_pipeline.cli run-all --channel CH06 --video 033`

### srt2images（CapCut/画像）向け補足
- THINK MODE 時の cues 計画は `visual_image_cues_plan` に集約（複数回 stop/resume しないため）。
- 任意: `SRT2IMAGES_CUES_PLAN_MODE=plan` を設定すると、通常モードでも cues 計画を single-task 経路へ寄せられる。
- 任意: `SRT2IMAGES_TARGET_SECTIONS=12` のように指定すると、cues 計画の目標セクション数を上書きできる（最低5）。
- 任意: `SRT2IMAGES_FORCE_CUES_PLAN=1` を設定すると、既存の `visual_cues_plan.json` を無視して再生成する（SRTが変わった/プランを作り直したい時）。
- 任意: `SRT2IMAGES_VISUAL_BIBLE_PATH=/abs/or/repo/relative/path.json` を指定すると、Visual Bible を外部ファイルから読み込める（デフォルトは pipeline が in-memory で渡す）。

### Stock B-roll（フリー動画）向け補足
必要キー（provider有効時のみ）:
- `PEXELS_API_KEY`（pixel/pexels）
- `PIXABAY_API_KEY`（pixabay）
- `COVERR_API_KEY`（coverr）

容量/再利用（推奨デフォルト）:
- `YTM_BROLL_FILE_CACHE`（default: `1`）: mp4本体を共有キャッシュし、run_dir へは hardlink で再利用する（重複DL/重複保存を抑制）
  - cache path: `workspaces/video/_state/stock_broll_cache/<provider>/files/*.mp4`
- `YTM_BROLL_MAX_W`（default: `1280`）, `YTM_BROLL_MAX_H`（default: `720`）: 候補選定で **過大解像度（例: 1080p/4K）を避ける**ための上限
- `YTM_BROLL_MIN_BYTES`（default: `50000`）: 壊れた/空のmp4をキャッシュヒット扱いしないための最小サイズ

## 重要ルール: 非`script_*` は API LLM が死んだら THINK MODE で続行（`script_*` は例外）
- デフォルト: **有効**（未設定でもON）
- 無効化（**デバッグ専用**）: `LLM_EXEC_SLOT=5`（api_failover_off。非scriptのみ。`script_*` は例外で停止）
  - **ロックダウン中（`YTM_ROUTING_LOCKDOWN=1`）は非`script_*` の failover は必ずON**（絶対ルール / OFFにできない）
  - OFF にする必要があるのは緊急デバッグ時のみ（`YTM_EMERGENCY_OVERRIDE=1` の上で使う）
- 互換/緊急: `LLM_API_FAILOVER_TO_THINK=0`（通常運用のロックダウンONでは停止。使うなら `YTM_EMERGENCY_OVERRIDE=1`）
- `LLM_FAILOVER_MEMO_DISABLE=1`（任意）: フォールバック時の全体向け memo 自動作成を無効化

### 失敗時に見る場所
- pending: `workspaces/logs/agent_tasks/pending/*.json`（または `LLM_AGENT_QUEUE_DIR`）
- memo: `workspaces/logs/agent_tasks/coordination/memos/*.json`（一覧は `python scripts/agent_org.py memos`）

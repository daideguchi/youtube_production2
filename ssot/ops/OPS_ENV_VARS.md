# OPS_ENV_VARS — 環境変数とキーの原則

## 原則
- 秘密鍵はリポジトリ直下の `.env` もしくはシェル環境変数に一元管理する。`.gemini_config` や `credentials/` 配下への複製は禁止。
- `.env.example` をベースに必要キーを埋める。既にシェルで export 済みの値があればそちらが優先される。
- グローバルに `PYTHONPATH` を固定しない（特に旧リポジトリ配下を含むと、誤importで事故りやすい）。必要なら `./scripts/with_ytm_env.sh ...` を使う。

## 主な必須キー（抜粋）
- Gemini: `GEMINI_API_KEY`（画像/テキスト共通）
- Fireworks（画像）: `FIREWORKS_API_KEY`（画像生成 / 既存）
- Fireworks（台本/本文）: `FIREWORKS_SCRIPT_API_KEY`（文章執筆 / LLMRouter provider=fireworks 用）
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

- `YTM_WEB_SEARCH_PROVIDER`（default: `auto`）:
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
- `SCRIPT_VALIDATION_LLM_REBUILD_ON_FAIL`（default: `0`）: Fixerで収束しない場合に、最終手段として「設計→再執筆（Rebuild）」を試す。無効化は `0`（既定OFF）。
- `SCRIPT_VALIDATION_QUALITY_REBUILD_PLAN_TASK`（default: `script_a_text_rebuild_plan`）: Rebuildの「設計図（JSON）生成」task key。
- `SCRIPT_VALIDATION_QUALITY_REBUILD_DRAFT_TASK`（default: `script_a_text_rebuild_draft`）: Rebuildの「本文生成」task key。

注:
- task key の実体（tier/model/options）は `configs/llm_router.yaml` と `configs/llm_task_overrides.yaml` を正とする。
- `SCRIPT_PIPELINE_DRY=1` のときは品質ゲートを走らせない（dry-run）。

## Script pipeline: 意味整合（Semantic alignment gate）
`packages/script_pipeline/runner.py` の `script_outline`（事前）と `script_validation`（最終）に適用される。

- `SCRIPT_OUTLINE_SEMANTIC_ALIGNMENT_GATE`（default: `1`）: アウトライン段階の事前意味整合ゲートを有効化（章草稿=高コストの前に逸脱を止める）。
- `SCRIPT_VALIDATION_SEMANTIC_ALIGNMENT_GATE`（default: `1`）: `script_validation` の意味整合ゲートを有効化。
- `SCRIPT_VALIDATION_SEMANTIC_ALIGNMENT_REQUIRE_OK`（default: `0`）: 合格条件を制御。
  - `0`: `verdict: major` のみ停止（ok/minor は合格; 量産デフォルト）
  - `1`: `verdict: ok` 以外は停止（minor/major は停止; より厳密にブロック）
- `SCRIPT_VALIDATION_SEMANTIC_ALIGNMENT_AUTO_FIX`（default: `1`）: `script_validation` 内で最小リライト（auto-fix）を試す。
- `SCRIPT_VALIDATION_SEMANTIC_ALIGNMENT_AUTO_FIX_MINOR`（default: `0`）: minor の auto-fix を許可（`minor -> ok` を狙って1回だけシャープにする用途）。必要なときだけ `1`。
- `SCRIPT_VALIDATION_SEMANTIC_ALIGNMENT_AUTO_FIX_MAJOR`（default: `1`）: major の auto-fix を許可。
- `SCRIPT_VALIDATION_SEMANTIC_ALIGNMENT_MAX_FIX_ATTEMPTS`（default: `1`）: auto-fix リトライ回数（最大2）。
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
- **Codex exec に回さない task は `script_chapter_draft` のみ**（Aテキスト本文の章草稿/本文執筆）。それ以外の `script_*` は Codex exec 優先（失敗時は LLMRouter API へフォールバック）。
  - 例外（固定）: `image_generation`, `web_search_openrouter` は Codex exec 対象外（既定でも除外）。
  - 運用上「このtaskは本文品質のためAPIに寄せたい」などがあれば、`configs/codex_exec.yaml: selection.exclude_tasks` に追加して局所的に外す。

### 設定（SoT）
- `configs/codex_exec.yaml`（tracked）
- 任意のローカル上書き: `configs/codex_exec.local.yaml`

### 環境変数（任意）
- `YTM_CODEX_EXEC_ENABLED`（override）: `1` で強制ON / `0` で強制OFF（未設定なら `configs/codex_exec.yaml` と `CODEX_MANAGED_BY_NPM` に従う）
- `YTM_CODEX_EXEC_DISABLE`（default: `0`）: `1` で強制OFF（緊急停止用）
- `YTM_CODEX_EXEC_ENABLE_IN_PYTEST`（default: `0`）: `1` のときだけ pytest 中の Codex exec を許可（既定はテスト安定のためOFF）
- `YTM_CODEX_EXEC_PROFILE`（default: `claude-code`）: `codex exec --profile` に渡すプロファイル名
- `YTM_CODEX_EXEC_MODEL`（任意）: `codex exec -m` に渡すモデル名
- `YTM_CODEX_EXEC_TIMEOUT_S`（default: `180`）: `codex exec` のtimeout（秒）
- `YTM_CODEX_EXEC_SANDBOX`（default: `read-only`）: `codex exec --sandbox`（運用では read-only 固定推奨）

## Script pipeline: Planning整合（内容汚染の安全弁）
- `SCRIPT_BLOCK_ON_PLANNING_TAG_MISMATCH`（default: `0`）: Planning 行が `tag_mismatch` の場合に高コスト工程の前で停止する（strict運用）。既定は停止せず、汚染されやすいテーマヒントだけ落として続行する（タイトルは常に正）。

## Script pipeline: エピソード重複（採用済み回と被せない）
- `SCRIPT_BLOCK_ON_EPISODE_DUPLICATION`（default: `0`）: 採用済み（Planning CSV の `進捗=投稿済み/公開済み` または `published_lock=true`（UI の `投稿完了`））の回と `キーコンセプト` が重複する場合、`topic_research/script_outline/script_draft` 等の高コスト工程の前で停止する（strict運用）。既定は停止せず、lint警告のみ。

## Agent-mode / THINK MODE（API LLM をエージェント運用へ置換）
Runbook/キュー運用の正本: `ssot/plans/PLAN_AGENT_MODE_RUNBOOK_SYSTEM.md`, `ssot/agent_runbooks/README.md`

### 切替
- `LLM_MODE`:
  - `api`（デフォルト）: 通常どおり API LLM を呼ぶ
  - `agent`: LLM 呼び出しを止めて `workspaces/logs/agent_tasks/` に pending を作る
  - `think`: `agent` の別名（THINK MODE）。フィルタ未指定なら `script_/tts_/visual_/title_/belt_` を安全デフォルトで intercept（`image_generation` 等は除外）

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
  - `export LLM_MODE=agent`
  - `export LLM_AGENT_TASK_PREFIXES=script_`
  - `python -m script_pipeline.cli run-all --channel CH06 --video 033`

### srt2images（CapCut/画像）向け補足
- THINK MODE 時の cues 計画は `visual_image_cues_plan` に集約（複数回 stop/resume しないため）。
- 任意: `SRT2IMAGES_CUES_PLAN_MODE=plan` を設定すると、通常モードでも cues 計画を single-task 経路へ寄せられる。
- 任意: `SRT2IMAGES_TARGET_SECTIONS=12` のように指定すると、cues 計画の目標セクション数を上書きできる（最低5）。
- 任意: `SRT2IMAGES_FORCE_CUES_PLAN=1` を設定すると、既存の `visual_cues_plan.json` を無視して再生成する（SRTが変わった/プランを作り直したい時）。
- 任意: `SRT2IMAGES_VISUAL_BIBLE_PATH=/abs/or/repo/relative/path.json` を指定すると、Visual Bible を外部ファイルから読み込める（デフォルトは pipeline が in-memory で渡す）。

## 重要ルール: API LLM が死んだら THINK MODE で続行
- `LLM_API_FAILOVER_TO_THINK`（任意）:
  - **デフォルト有効**（未設定でもON）
  - API LLM が失敗したら、自動で pending を作って停止（= THINK MODEで続行できる状態にする）
  - 無効化: `LLM_API_FAILOVER_TO_THINK=0`
- `LLM_FAILOVER_MEMO_DISABLE=1`（任意）: フォールバック時の全体向け memo 自動作成を無効化

### 失敗時に見る場所
- pending: `workspaces/logs/agent_tasks/pending/*.json`（または `LLM_AGENT_QUEUE_DIR`）
- memo: `workspaces/logs/agent_tasks/coordination/memos/*.json`（一覧は `python scripts/agent_org.py memos`）

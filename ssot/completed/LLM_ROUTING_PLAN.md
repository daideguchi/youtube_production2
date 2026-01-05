# LLM 呼び出しレイヤ現状整理と段階的リファイン案

> 重要（現行SSOT）:
> - この文書は過去の整理/検討メモ（completed）です。**現行運用の正本ではありません**。
> - 現行のモデル選択SSOTは `ssot/ops/OPS_LLM_MODEL_CHEATSHEET.md` / `ssot/ops/OPS_ENV_VARS.md`。
> - モデル切替は **数字スロット** `LLM_MODEL_SLOT`（`configs/llm_model_slots.yaml`）に統一されています。

## 1. 現状の実装サマリ

- **集計ツール**: `python3 scripts/aggregate_llm_usage.py --log workspaces/logs/llm_usage.jsonl --top 10` でモデル/タスク別件数や fallback_chain, 平均レイテンシを確認。詳細は `ssot/ops/TOOLS_LLM_USAGE.md`。
### 1.1 設定ファイルの分布
- **モデル仕様レジストリ**: `configs/llm_model_registry.yaml` に provider / endpoint / API バージョン / responses API 利用有無 / パラメータ許容フラグ（temperature, stop, reasoning）と default_max_completion_tokens を保持。Azure の gpt-5 系、OpenRouter の無料 Llama、Gemini 互換枠などが混在し、`fallback_model` もここで定義。【F:configs/llm_model_registry.yaml†L1-L79】
- **タスク割り当て**: `configs/llm_registry.json` で research / review / script_* / image_generation など業務タスク→provider, model を一対一で紐付け。大半が Azure gpt-5-mini 固定で、thinkingLevel や max_output_tokens が一部タスクに直書きされている。【F:configs/llm_registry.json†L1-L96】
- **OpenRouter メタ**: `packages/script_pipeline/config/openrouter_models.json` は多数モデルのサポートパラメータと max_tokens を持つが、現行ランナーでは参照は限定的。【F:packages/script_pipeline/config/openrouter_models.json†L1-L115】
- **TTS タスクマップ**: `packages/audio_tts/configs/llm_tasks.yaml` で annotate/reading など TTS サブタスクを `tts_primary` に束ね、呼び出しは `LLMRouter/LLMClient` に移行済み（旧 `llm_client` は廃止予定）。【F:packages/audio_tts/configs/llm_tasks.yaml†L1-L7】【F:packages/audio_tts/tts/llm_adapter.py†L1-L120】
- **フォールバックポリシー**: `configs/llm_fallback_policy.yaml` で transient ステータス／リトライ上限／backoff に加え per-status backoff・per-status retry 上限、最大試行回数、総待機時間を設定化（デフォルト: 429/5xx/408, 全候補試行, backoff=1s, wait<=30s）。
- **tier候補/タスク割当のオーバーライド**: `configs/llm_tier_candidates.yaml` / `configs/llm_tier_mapping.yaml` で tier→モデル候補・task→tier を上書き可能。image tier は `configs/image_models.yaml`（ImageClient）で管理。

### 1.2 実行経路（packages/script_pipeline/runner.py）
- ステージ定義 (`stages.yaml`/`templates.yaml`) からテンプレートと model/provider を解決し、`_run_llm` が直接プロバイダ別の関数を叩く。`SCRIPT_PIPELINE_DEFAULT_MODEL` と registry の `default_model`、または最初のモデルがフォールバックとして使われる。【F:packages/script_pipeline/runner.py†L1213-L1302】
- **Azure**: `use_responses_api` が true なら `/openai/responses` を、false なら chat/completions を呼ぶ。`force_responses`（script_draft_format 用）や `force_chat` で切替、`reasoning_effort` は minimal をデフォルト注入。環境変数 `AZURE_OPENAI_API_KEY/ENDPOINT` を必須とし、cognitiveservices ドメインを強制検証。【F:packages/script_pipeline/runner.py†L345-L463】
- **OpenRouter**: 単純な chat/completions 呼び出し。`max_tokens` とメッセージのみを送信し、エラー時のフォールバックはなし。【F:packages/script_pipeline/runner.py†L465-L509】
- **Gemini**: v1beta generateContent を直叩き。`thinkingLevel` を generationConfig.thinkingConfig に設定し、quota 429/RESOURCE_EXHAUSTED で registry の `fallback_model` に自動スイッチ（Azure 呼び直し）する特例あり。【F:packages/script_pipeline/runner.py†L510-L620】【F:packages/script_pipeline/runner.py†L1352-L1421】
- ログ: ステージごとに prompt/response を `workspaces/logs/` 下へ書き出し。`_normalize_llm_output` で章ドラフト JSON unwrap などの後処理を行う。

### 1.3 個別クライアント（packages/audio_tts）
- 現在は `LLMRouter/LLMClient` を使用し、task→tier→model で解決（`llm_client.py` は段階的削除予定）。TTS側の呼び出しは `packages/audio_tts/tts/llm_adapter.py` が集約。
- 旧 `azure_responses/azure_chat_with_fallback` ベースの `llm_client.py` は capability 正規化や tier 抽象がなく、段階的に掃除中。

- **タスク別オーバーライド**: `configs/llm_task_overrides.yaml` で taskごとに tier/models/options/system_prompt を簡単に上書き可能（デフォルトは llm_router.yaml）。tier候補は `configs/llm_tier_candidates.yaml` / `llm_tier_mapping.yaml` で調整、image tier は `configs/image_models.yaml`（ImageClient）。

### 1.4 現状課題・最新アップデート
- フォールバックポリシーを `configs/llm_fallback_policy.yaml` で設定化（429/5xx/408 のみ次候補、backoff 1s、retry_limit=0=全候補）。llm_router が参照。
- 成功/失敗の呼び出しログを `workspaces/logs/llm_usage.jsonl`（環境変数 `LLM_ROUTER_LOG_PATH`、`LLM_ROUTER_LOG_DISABLE=1` で無効化）へ JSONL 追記する仕組みを追加。成功時は `task/model/provider/fallback_chain/latency_ms/usage(token)/request_id`、失敗時は `error/error_class/status_code/chain` を記録。
- タスク→モデルが直結しており、利用層（reasoning 重視 / コスト重視 / 画像）の抽象がないため、一括でモデル方針を切り替えるのが困難。【F:configs/llm_registry.json†L1-L96】
- Azure Responses API 前提で temperature/stop を送らないなどの制約がコード側に散在し、OpenRouter/Gemini との差異を吸収する統一インターフェースがない。【F:packages/script_pipeline/runner.py†L345-L463】【F:packages/audio_tts/tts/llm_client.py†L145-L180】
- Fallback は Gemin→Azure など特定経路に限定され、quota / 429 検知による段階的フォールバックや tier 代替案が設計されていない。【F:packages/script_pipeline/runner.py†L1352-L1421】
- パラメータ互換（reasoning_effort vs reasoning, max_completion_tokens vs max_tokens, response_format 等）の正規化がなく、モデル追加時の記述が煩雑。
- OpenRouter 側の capability 情報（`openrouter_models.json`）がランナーで参照されず、structured_outputs/response_format 対応や max_tokens の制約を自動検証できていない。【F:packages/script_pipeline/config/openrouter_models.json†L1-L115】
- audio_tts 系は `llm_tasks.yaml` ベースで registry を読むが script_pipeline と tier 抽象を共有しておらず、モデル選択ポリシーが分断されている。【F:packages/audio_tts/configs/llm_tasks.yaml†L1-L7】【F:packages/audio_tts/tts/llm_client.py†L16-L180】

## 2. 理想像に向けた設計方針（提案）
本タスクでは大規模実装は行わず、移行を見据えた設計案をまとめる。

### 2.1 3段階ルーティングの導入
1. **task layer**: 業務ステップ（script_draft, script_rewrite, tts_text_prepare, image_chunking 等）を定義。既存の `stages.yaml` / `llm_registry.json` タスクキーを出発点にする。
2. **tier layer**: 抽象的な性能/コスト帯を表すキー（例: heavy_reasoning / standard / cheap / image / speech）。タスク→tier マップを config 化し、チャンネル/運用ごとに override 可能にする（例: `configs/llm_tiers.yaml`）。
3. **physical model layer**: provider+model の組み合わせ（azure/gpt-5-mini, openrouter/kimi-k2-thinking, openrouter/deepseek-v3, gemini-image など）を tier ごとに複数候補で保持。優先順/利用条件（max_tokens, structured_outputs, reasoning 可否）を併記。

### 2.2 レジストリの再編
- `llm_model_registry.yaml` を「モデル能力カタログ」として維持しつつ、以下を追加フィールドとして推奨:
  - `capabilities`: structured_outputs, vision, image, tts, tools, reasoning_level(s)
  - `cost_tier`: cheap/standard/premium など相対コスト指標
  - `params`: プロバイダ固有パラメータの正規化ルール（例: `max_tokens_key`, `reasoning_key`, `response_format_support`）。
- 新規ファイル案 `configs/llm_tier_mapping.yaml`:
  - `tasks:` で task→tier（デフォルト）を定義。
  - `overrides:` で channel/環境別に tier やモデル候補を上書き可能にする。
- 新規ファイル案 `configs/llm_tier_candidates.yaml`:
  - tier→[model keys] の優先リストを保持し、fallback 順序を明示。
  - 例: `heavy_reasoning: [openrouter/deepseek-v3.2-exp, openrouter/kimi-k2-thinking]`。

### 2.3 呼び出しパイプラインの分離と正規化
- **抽象クライアント**を用意し、provider 固有処理（Azure responses/chat, OpenRouter, Gemini）を adapter に閉じ込める。呼び出し前に capability/param resolver が `max_tokens_key` や `reasoning` の表現を統一し、送信 payload を生成。
- **param guard**: registry の allow_* フラグを使い、禁止された temperature/stop を自動で落とす。structured outputs や image generation も capabilities に基づき事前検証。
- **fallback policy**: tier 候補リストに従い、429/5xx/timeout をトリガに段階的に切替。Azure→OpenRouter→Azure などクロスプロバイダの優先度を設定可能にする。
- **telemetry/logging**: 現行の prompt/response ログに加え、選択された tier/model・fallback 経路・発火理由を JSONL に記録し、後続分析を容易にする。

### 2.4 タスク別ロールと互換性
- **reasoning 重視タスク**（script_draft, script_rewrite, quality_review 等）には tier=heavy_reasoning を割り当て、長文/context 長上限を capabilities でフィルタリング。
- **コスト重視タスク**（caption, thumbnail_caption, belt_generation, image_chunking 等）は tier=cheap をデフォルトにし、max_tokens 既定値を短くする。
- **画像/音声**は tier=image/speech として分離し、Gemini image API や TTS モデルを混在させないよう interface を分ける。
- **メッセージ構造**: script_draft_format のような system/user 分離テンプレは as_messages を強制するプリプロセスとして扱い、モデルに依存しない整形ステップに落とし込む。

### 2.5 移行ステップの提案
1. **現行 config の写経**: `llm_registry.json` のタスク→モデル対応を `llm_tier_mapping.yaml` に移し、暫定的に tier=standard に全タスクを紐付ける。
2. **tier 候補の初期化**: `llm_tier_candidates.yaml` で heavy_reasoning/standard/cheap/image/speech の優先リストを記述（現状モデルキーをそのまま利用）。
3. **adapter 層のプロトタイプ**: `packages/script_pipeline/llm/` などに provider adapters と payload normalizer を配置し、現行 `_run_llm` から呼び出す薄い facade を追加（既存関数は温存）。
4. **フォールバック実験**: Gemini quota 時の Azure fallback ロジックを tier ベースに置き換え、429/5xx を検知して候補リストを順次試行するハンドラを導入。
5. **観測強化**: ログ JSONL に `task`, `tier`, `selected_model`, `fallback_chain`, `reason` を記録。後続でコスト/品質計測を行い、tier 再配置の判断材料にする。

## 3. 期待される効果
- タスク群を tier で束ねることで「台本生成は heavy_reasoning を第一候補に」「字幕整形は standard or cheap を優先」といったポリシー変更を config だけで実現。
- プロバイダ固有パラメータの正規化により、新規モデル追加時の実装コストと事故（temperature 禁止モデルへの誤送信など）を削減。
- フォールバックポリシーとログ強化により、quota/障害時の自動切替とその可視化が可能になり、運用耐性を向上。

## 4. 次の一手（軽量タスク案）
- `configs/llm_tier_mapping.yaml` / `configs/llm_tier_candidates.yaml` のスケルトンを追加し、現行設定を移し替えるドラフトを作成。
- `script_pipeline` と `audio_tts` で共通に使える provider adapter インターフェースを設計（関数シグネチャと期待する normalized options を明文化）。
- Azure Responses / OpenRouter / Gemini でのパラメータ対応表を作り、registry `capabilities` に落とし込む（response_format, reasoning, max_tokens キー差異など）。

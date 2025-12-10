# LLM 呼び出しレイヤ現状整理と段階的リファイン案

## 1. 現状の実装サマリ

### 1.1 設定ファイルの分布
- **モデル仕様レジストリ**: `configs/llm_model_registry.yaml` に provider / endpoint / API バージョン / responses API 利用有無 / パラメータ許容フラグ（temperature, stop, reasoning）と default_max_completion_tokens を保持。Azure の gpt-5 系、OpenRouter の無料 Llama、Gemini 互換枠などが混在し、`fallback_model` もここで定義。【F:configs/llm_model_registry.yaml†L1-L79】
- **タスク割り当て**: `configs/llm_registry.json` で research / review / script_* / image_generation など業務タスク→provider, model を一対一で紐付け。大半が Azure gpt-5-mini 固定で、thinkingLevel や max_output_tokens が一部タスクに直書きされている。【F:configs/llm_registry.json†L1-L96】
- **OpenRouter メタ**: `script_pipeline/config/openrouter_models.json` は多数モデルのサポートパラメータと max_tokens を持つが、現行ランナーでは参照は限定的。【F:script_pipeline/config/openrouter_models.json†L1-L115】
- **TTS タスクマップ**: `audio_tts_v2/configs/llm_tasks.yaml` で annotate/reading など TTS サブタスクを `tts_primary` に束ね、`llm_client` が registry と組み合わせて解決する。【F:audio_tts_v2/configs/llm_tasks.yaml†L1-L7】【F:audio_tts_v2/tts/llm_client.py†L16-L109】

### 1.2 実行経路（script_pipeline/runner.py）
- ステージ定義 (`stages.yaml`/`templates.yaml`) からテンプレートと model/provider を解決し、`_run_llm` が直接プロバイダ別の関数を叩く。`SCRIPT_PIPELINE_DEFAULT_MODEL` と registry の `default_model`、または最初のモデルがフォールバックとして使われる。【F:script_pipeline/runner.py†L1213-L1302】
- **Azure**: `use_responses_api` が true なら `/openai/responses` を、false なら chat/completions を呼ぶ。`force_responses`（script_draft_format 用）や `force_chat` で切替、`reasoning_effort` は minimal をデフォルト注入。環境変数 `AZURE_OPENAI_API_KEY/ENDPOINT` を必須とし、cognitiveservices ドメインを強制検証。【F:script_pipeline/runner.py†L345-L463】
- **OpenRouter**: 単純な chat/completions 呼び出し。`max_tokens` とメッセージのみを送信し、エラー時のフォールバックはなし。【F:script_pipeline/runner.py†L465-L509】
- **Gemini**: v1beta generateContent を直叩き。`thinkingLevel` を generationConfig.thinkingConfig に設定し、quota 429/RESOURCE_EXHAUSTED で registry の `fallback_model` に自動スイッチ（Azure 呼び直し）する特例あり。【F:script_pipeline/runner.py†L510-L620】【F:script_pipeline/runner.py†L1352-L1421】
- ログ: ステージごとに prompt/response を `logs/` 下へ書き出し。`_normalize_llm_output` で章ドラフト JSON unwrap などの後処理を行う。

### 1.3 個別クライアント（audio_tts_v2）
- `get_model_conf` で registry から provider/endpoint/deployment を解決し、Azure の場合は endpoint 正規化と verbosity 推定を実施。Responses API を前提とした `azure_responses` がメインで、Gemini も fallback としてサポートしている。【F:audio_tts_v2/tts/llm_client.py†L84-L180】
- `azure_chat_with_fallback` は model_keys を受け取り順次試行するが、パラメータ互換性チェックや tier 抽象は存在しない。【F:audio_tts_v2/tts/llm_client.py†L198-L272】

### 1.4 現状課題
- タスク→モデルが直結しており、利用層（reasoning 重視 / コスト重視 / 画像）の抽象がないため、一括でモデル方針を切り替えるのが困難。【F:configs/llm_registry.json†L1-L96】
- Azure Responses API 前提で temperature/stop を送らないなどの制約がコード側に散在し、OpenRouter/Gemini との差異を吸収する統一インターフェースがない。【F:script_pipeline/runner.py†L345-L463】【F:audio_tts_v2/tts/llm_client.py†L145-L180】
- Fallback は Gemin→Azure など特定経路に限定され、quota / 429 検知による段階的フォールバックや tier 代替案が設計されていない。【F:script_pipeline/runner.py†L1352-L1421】
- パラメータ互換（reasoning_effort vs reasoning, max_completion_tokens vs max_tokens, response_format 等）の正規化がなく、モデル追加時の記述が煩雑。
- OpenRouter 側の capability 情報（`openrouter_models.json`）がランナーで参照されず、structured_outputs/response_format 対応や max_tokens の制約を自動検証できていない。【F:script_pipeline/config/openrouter_models.json†L1-L115】
- audio_tts 系は `llm_tasks.yaml` ベースで registry を読むが script_pipeline と tier 抽象を共有しておらず、モデル選択ポリシーが分断されている。【F:audio_tts_v2/configs/llm_tasks.yaml†L1-L7】【F:audio_tts_v2/tts/llm_client.py†L16-L180】

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
  - 例: `heavy_reasoning: [openrouter/kimi-k2-thinking, openrouter/deepseek-r1, azure/gpt-5-chat]`。

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
3. **adapter 層のプロトタイプ**: `script_pipeline/llm/` などに provider adapters と payload normalizer を配置し、現行 `_run_llm` から呼び出す薄い facade を追加（既存関数は温存）。
4. **フォールバック実験**: Gemini quota 時の Azure fallback ロジックを tier ベースに置き換え、429/5xx を検知して候補リストを順次試行するハンドラを導入。
5. **観測強化**: ログ JSONL に `task`, `tier`, `selected_model`, `fallback_chain`, `reason` を記録。後続でコスト/品質計測を行い、tier 再配置の判断材料にする。

## 3. 期待される効果
- タスク群を tier で束ねることで「台本生成は heavy_reasoning を第一候補に」「字幕整形は standard or cheap を優先」といったポリシー変更を config だけで実現。
- プロバイダ固有パラメータの正規化により、新規モデル追加時の実装コストと事故（temperature 禁止モデルへの誤送信など）を削減。
- フォールバックポリシーとログ強化により、quota/障害時の自動切替とその可視化が可能になり、運用耐性を向上。

## 4. 次の一手（軽量タスク案）
- `configs/llm_tier_mapping.yaml` / `configs/llm_tier_candidates.yaml` のスケルトンを追加し、現行設定を移し替えるドラフトを作成。
- `script_pipeline` と `audio_tts_v2` で共通に使える provider adapter インターフェースを設計（関数シグネチャと期待する normalized options を明文化）。
- Azure Responses / OpenRouter / Gemini でのパラメータ対応表を作り、registry `capabilities` に落とし込む（response_format, reasoning, max_tokens キー差異など）。

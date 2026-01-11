# LLM レイヤー再設計プラン (SSOT)

> 重要（現行SSOT）:
> - この文書は過去の再設計プラン（completed）です。**現行運用の正本ではありません**。
> - 現行のモデル選択SSOTは `ssot/ops/OPS_LLM_MODEL_CHEATSHEET.md` / `ssot/ops/OPS_ENV_VARS.md`。
> - モデル切替は **数字スロット** `LLM_MODEL_SLOT`（`configs/llm_model_slots.yaml`）に統一されています。

## 1. 目的
- 本リポジトリでは台本生成・TTS 補助・画像用チャンクなどで LLM を広範に利用しており、多くが **Azure gpt-5-mini 固定** かつタスク側ベタ書きになっている。【F:configs/llm_registry.json†L1-L74】
- 台本品質を上げるには強い reasoning モデルをタスク単位で確実に使い分ける必要がある一方、TTS・画像系はコスト優先でよい。
- Azure Responses API / OpenRouter / Gemini など **プロバイダごとのパラメータ差異** があり、現状は安全に吸収できていない。
- 目標は **task → tier → physical model** の 3 段階ルーティングと **capabilities に基づくパラメータ自動調整** を備えた共通クライアントを設計し、将来のモデル追加・切替を容易にすること。

## 2. 現状の構成と処理フロー（調査結果）
### 2.1 設定ファイルとレジストリ
- **現行で運用してよいモデル**: テキストは `azure/gpt-5-mini`、画像は `gemini-2.5-flash-image` のみ。存在しない/旧名モデルは使わない（設定に残っていれば掃除対象）。
- **登録ポリシー**: レジストリには将来使う候補（OpenRouter など）を残してよいが、運用で使うかどうかは tier リストで制御する。現状の tier は gpt-5-mini／gemini-2.5-flash-image のみに絞っており、他候補は「登録のみ・未運用」。
- `configs/llm_registry.json`: 業務タスク→provider/model を 1:1 で紐付け。台本系・TTS 系ともほぼ全て `azure/gpt-5-mini` 固定。thinkingLevel / max_output_tokens がタスクに直書きされている。【F:configs/llm_registry.json†L1-L74】
- `configs/llm_model_registry.yaml`: モデル仕様レジストリ。Azure（gpt-5-mini / gpt-5-chat / tts_primary / gemini-3-pro-preview）と OpenRouter 無料枠、OpenAI 互換を定義。Azure の `use_responses_api` や `allow_reasoning_effort` フラグを持つが、family は chat に固定、image 系は未整備。【F:configs/llm_model_registry.yaml†L1-L71】
- `configs/llm_router.yaml`: 別系統の Router 設定。providers（azure/openrouter/gemini）、models、tiers（heavy_reasoning/standard/cheap/image_gen）、tasks を定義。台本・TTS 向けの tier 設計があるが、実装側の capabilities 反映は限定的。【F:configs/llm_router.yaml†L1-L86】
- `configs/prompt_config.yaml`: Remotion 用ベルト生成などで参照される prompt 断片を管理。モデル指定はないが、タスク名と密結合した期待出力形式があり、Router 切替時の互換確認が必要。【F:configs/prompt_config.yaml†L1-L84】

#### 現在のタスク→モデル紐付け主要例
- **台本系**: `script_draft` / `script_rewrite` / `script_polish_ai` などは `llm_registry.json` で `azure/gpt-5-mini` 固定。thinkingLevel=high などをタスク側で指定し、上書き不可。
- **品質チェック系**: `quality_review` / `context_analysis` なども `azure/gpt-5-mini` 固定。
- **TTS 補助**: `tts_annotate` / `tts_reading` / `audio_text` も gpt-5-mini。max_tokens をタスクに直記。
- **画像関連**: Router 側で `image_gen` tier を Gemini 固定で定義しているが、テキスト prompt 抽出のみで capability 定義なし。

### 2.2 呼び出しコード
- `packages/factory_common/llm_router.py`: シングルトン Router。`configs/llm_router.yaml` を読み、tier→model 解決後に Azure/OpenRouter/Gemini（image 限定）を叩く。Azure/OR は `chat.completions.create` を直接呼び、reasoning モデル向けには温度系パラメータを除外する簡易ロジックのみ。Gemini text は未実装。image は Gemini のみで prompt を messages から抽出し、そのまま `generate_content` を呼ぶ暫定実装。【F:packages/factory_common/llm_router.py†L1-L191】【F:packages/factory_common/llm_router.py†L240-L309】
- `packages/audio_tts/tts/llm_adapter.py`: TTS 注釈・読み分けで Router を呼ぶ。`task="tts_annotate"/"tts_reading"` などを指定し、JSON 応答を期待。Router 失敗時は手作りデフォルトにフォールバック。【F:packages/audio_tts/tts/llm_adapter.py†L75-L154】【F:packages/audio_tts/tts/llm_adapter.py†L185-L245】
- `apps/remotion/scripts/gen_belt_from_srt.js`: ベルト文言生成で環境変数または JSON に埋め込んだモデル（デフォルト `gpt-5-mini`）を使用。llm_router ではなく直接設定を参照。【F:apps/remotion/scripts/gen_belt_from_srt.js†L98-L120】
- `apps/ui-backend/backend` まわり: `core/llm.py` はテスト用スタブのみで実運用呼び出しは未実装。【F:apps/ui-backend/backend/core/llm.py†L1-L30】

### 2.3 処理フロー例（詳細）
#### 台本生成系（script_*）
1. `script_pipeline` / `scripts` 配下からタスク呼び出し（例: `script_draft`）。
2. `configs/llm_registry.json` を直接参照し、`provider=azure`, `deployment=gpt-5-mini` を取得。【F:configs/llm_registry.json†L1-L74】
3. Azure SDK の `responses` API に `messages` と `max_output_tokens` を送信（Router 経由であっても payload はタスクが指定）。【F:factory_common/llm_router.py†L65-L155】
4. 応答の text をそのまま後続のポストプロセスに渡し、usage は場所によっては破棄。
5. thinking_level / reasoning_effort は「タスクに設定されていれば送る」だけで、モデル側 capability に基づく抑制はなし。

#### TTS 用注釈
1. `packages/audio_tts/tts/llm_adapter.py` の `call_llm()` が `get_router()` を呼び出す。
2. task=`tts_annotate`（読み分け指示生成） or `tts_reading`（強弱/間の挿入）を指定し、messages を投げる。【F:audio_tts/tts/llm_adapter.py†L185-L245】
3. `llm_router` は tier `standard`→model `azure/gpt-5-mini` を返す。
4. `response_format=json` を期待しているが、Router 側は validation せずにそのまま渡す。Azure 側で 400/422 になると例外となり、`call_llm()` でハードフォールバック。
5. 生成結果 JSON を Python dict として受け取り、TTS チャンクへ埋め込む。usage は無視。

#### 画像生成（ベルト・挿絵向け）
1. Remotion / commentary スクリプトから `llm_router` を image_gen tier で呼び出す。
2. messages の最後の user content を prompt とみなして `gemini-2.5-flash-image` の `generate_content` にそのまま渡す。【F:factory_common/llm_router.py†L240-L309】
3. aspect_ratio / n / seed といったパラメータは prompt テキストに埋め込むしかなく、型安全な指定は不可。
4. 戻り値は base64 → bytes に変換し、ファイル保存。usage/metadata は欠落。

#### JavaScript パス（ベルト生成）
1. `apps/remotion/scripts/gen_belt_from_srt.js` で `modelConfig` を環境変数または埋め込み JSON から取得。【F:apps/remotion/scripts/gen_belt_from_srt.js†L98-L120】
2. OpenAI 互換 API を直接叩き、結果テキストを字幕用のベルト文言として保存。
3. Router/SSOT 非依存のため、モデル切替時は JS 側の設定を別途変更する必要がある。

## 3. 現状の課題・リスク
1) **タスクとモデルが 1:1 固定**
- `configs/llm_registry.json` は task に provider/model をベタ紐付けし、tier/fallback なし。台本系も gpt-5-mini 固定で heavy_reasoning モデルを使う余地がない。
- リスク: モデル切替時にタスクごと修正が必要、品質向上・コスト調整の柔軟性が低い。【F:configs/llm_registry.json†L1-L74】

2) **capabilities ベースのパラメータ整合が弱い**
- `llm_model_registry.yaml` に allow_* フラグはあるが、実際の呼び出しでは temperature/stop 以外の検証がなく、Azure Responses API 固有フィールド（`max_completion_tokens` 等）の自動変換もない。
- リスク: OpenRouter/Responses API への不正パラメータで 400、推論挙動のブレが発生しうる。【F:factory_common/llm_router.py†L132-L207】

3) **image は Gemini 直叩きで SSOT 不足**
- prompt 抽出＋ `generate_content` の暫定コードのみで、サイズ・アスペクト・n・seed などの共通化がなく、他プロバイダ差し替え不可。【F:factory_common/llm_router.py†L240-L309】

4) **複数レジストリが併存し一貫性が弱い**
- `llm_registry.json` と `llm_router.yaml` / `llm_model_registry.yaml` が併存し、どれが正なのか不明瞭。UI/Remotion/音声で参照する設定が異なる。
- リスク: タスク間でモデル定義が乖離し、切替やデバッグが困難。

5) **共通インターフェースの欠如**
- 呼び出し側が `router.call` 直呼び・OpenAI SDK 直呼び・JS 実装などバラバラ。結果型/usage の扱いも統一されていない。
- リスク: ロギング・リトライ・パラメータ調整が散在し、品質/コストの見える化ができない。

6) **タスク出力契約とモデル設定の乖離**
- `configs/prompt_config.yaml` で想定する出力フォーマット（例: JSON 構造化、特定キー）が Router 設定や実際のモデル capability と同期されていない。
- リスク: モデル切替時に出力スキーマが壊れても検知できず、下流パイプラインでパースエラーや silent failure が発生。

## 4. 目標とするアーキテクチャ
### 4.1 設定構造 (例: `configs/llm.yml` に統合)
```yaml
models:
  azure_gpt5_mini:
    provider: azure
    family: chat
    deployment: gpt-5-mini
    api_version: 2025-03-01-preview
    endpoint: ${AZURE_OPENAI_ENDPOINT}
    use_responses_api: true
    capabilities:
      allow_reasoning_effort: true
      allow_temperature: false
      allow_stop: false
      max_completion_tokens: 128000
      supports_responses_api: true
  or_kimi_k2:
    provider: openrouter
    family: chat
    model: kimi/k2-thinking
    capabilities:
      allow_reasoning_effort: true
      allow_temperature: false
      allow_stop: false
      max_completion_tokens: 32000
      supports_json_mode: true
  gemini_flash_image:
    provider: gemini
    family: image
    model: gemini-2.5-flash-image
    capabilities:
      supports_aspect_ratio: true
      supports_n: true
      supports_seed: true
      supports_negative_prompt: true
```

```yaml
tiers:
  heavy_reasoning: [or_deepseek_v3_2_exp, or_kimi_k2_thinking]
  standard: [azure_gpt5_mini, or_qwen_free]
  cheap: [or_qwen_free]
  image: [gemini_flash_image, openai_gpt_image_1]

tasks:
  script_draft: { tier: heavy_reasoning, defaults: { thinking_level: high } }
  script_rewrite: { tier: heavy_reasoning }
  script_polish_ai: { tier: heavy_reasoning }
  tts_annotate: { tier: standard, defaults: { response_format: json_object } }
  tts_reading: { tier: standard, defaults: { response_format: json_object, max_output_tokens: 2048 } }
  image_generation: { tier: image, overrides: { aspect_ratio: "16:9", n: 1 } }
```

### 4.2 共通クライアント案（擬似コード）
```python
@dataclass
class LLMCallOptions:
    task: str
    thinking_level: str | None = None
    max_output_tokens: int | None = None
    response_format: Literal["text", "json_object"] | None = None
    timeout: int | None = None
    extra: dict | None = None

@dataclass
class LLMResult:
    content: str
    provider: str
    model: str
    usage: dict

class LLMClient:
    def call(self, messages: list[Message], options: LLMCallOptions) -> LLMResult:
        model_cfg = resolve_model(options.task)           # task → tier → model
        payload = normalize(messages, options, model_cfg) # capabilities に基づきパラメータ調整
        adapter = get_adapter(model_cfg.provider)
        raw = adapter.invoke(model_cfg, payload)
        return parse_result(raw)
```
- **normalize**: `allow_reasoning_effort` が false なら thinking_level を none に落とす、`allow_temperature=false` なら temperature を除去、`max_output_tokens` を provider 別に変換。
- **adapters**: `AzureClient` / `OpenRouterClient` / `GeminiClient` で HTTP 差分を吸収し、usage・request_id を返却。
- **image**: `ImageClient` も同構造で `ImageTaskOptions(task, prompt, aspect_ratio, n, extra)` → bytes[].

#### パラメータ正規化の具体例
- Azure Responses (gpt-5-mini):
  - `thinking_level=high` → `reasoning.effort="high"` に変換。capability 無しなら削除。
  - `max_output_tokens` → `max_output_tokens` (Responses) / fallback で `max_tokens` (Completions)。
  - `temperature` は capability false なら除去。
- OpenRouter chat:
  - `response_format=json_object` → `response_format={"type":"json_object"}` に変換。対応不可モデルでは除去し、警告ログ。
  - `stop` は `allow_stop=false` のモデルでは除去。
- Gemini text:
  - 未実装だが、`thinking_level` はサポートなしとして削除、`max_output_tokens` を `generationConfig.maxOutputTokens` へ。
- Image (Gemini/OpenAI/Stability):
  - 共通キー `aspect_ratio/size/n/seed/negative_prompt` を `param_mapping` で provider フィールドへマップし、非対応は削除。
  - バッチ上限 `max_batch_n` を超える場合は分割送信。

### 4.3 台本系 heavy_reasoning ポリシー
- tier `heavy_reasoning` の優先候補: `openrouter/deepseek-v3.2-exp` → `openrouter/kimi-k2-thinking`（2モデル固定）。
- コスト制約時は standard/cheap を先にダウングレードし、heavy_reasoning は最後に gpt-5-mini へフォールバックする優先順位を明記。

### 4.4 監視・テレメトリの統一
- `LLMResult` / `ImageResult` に `request_id`, `provider_latency_ms`, `usage` を必ず格納。
- task 単位で Prometheus メトリクス（success/fail/latency/token）を記録し、台本系と TTS 系でコスト比較できるようにする。
- 429/5xx は Router 層で指数バックオフ＋最大試行回数を統一し、モデルごとのリトライ可否を capabilities に持たせる。

## 5. 段階的移行プラン
1) **設定統合**
   - `configs/llm_registry.json` を段階的に廃止し、`configs/llm.yml`（models/tiers/tasks）へ統合。
   - 互換レイヤとして旧キーを新設定にマップする読み替え関数を一時的に用意。

2) **Router リプレース**
   - 新 `LLMClient` / `ImageClient` を `factory_common` に追加し、capabilities ベースの normalize を実装。
   - まず `packages/audio_tts/tts/llm_adapter.py` を新クライアント経由に切替え、JSON/timeout/usage を検証。

3) **台本パイプライン移行**
   - script 系バッチ（Python/JS）の呼び出しを順次 LLMClient に統一。タスク名のみを渡す形へ修正。
   - `apps/remotion/scripts/gen_belt_from_srt.js` など JS 側は HTTP 経由の共通 API か Python ラッパを用意し、モデル名直書きを排除。

4) **画像パイプライン整備**
   - `image_generation` 系を `ImageClient` に一本化し、`IMAGE_MODELS.yml`（別 PLAN 参照）で tier 管理。

5) **旧コード削除**
   - `llm_registry.json` 参照パスと旧 Router 呼び出しを段階的に除去し、最終的に `packages/factory_common/llm_router.py` を新実装に置換。

### 5.1 マイグレーション詳細（ステップ別チェックリスト）
- **Step 1: SSOT 集約**
  - 作業: `configs/llm.yml` を新設し、models/tiers/tasks を一元化。
  - 対象: `configs/llm_registry.json`, `configs/llm_model_registry.yaml`, `configs/llm_router.yaml`。
  - 検証: 旧設定と新設定の diff を CI で JSON 出力し、タスクごとの差異を確認。
  - ロールバック: 旧ファイルを残し、`LLMClient` に `LEGACY_LLM_CONFIG=true` フラグで旧読込に戻す。

- **Step 2: LLMClient 実装と TTS 切替**
  - 作業: `packages/factory_common/llm_client.py` を新設、Azure/OpenRouter/Gemini adapter を分離。`packages/audio_tts/tts/llm_adapter.py` の呼び出しを `LLMClient/LLMRouter` に変更（旧 `llm_client.py` は廃止済み）。
  - 検証: TTS 系ユニットテスト（JSON 返却、max_tokens、タイムアウト）を追加し、mock HTTP で 400/429/5xx を再現。
  - ロールバック: `llm_adapter.py` に旧 `get_router()` 呼び出しを残し、フラグで切替。

- **Step 3: 台本パイプライン統一**
  - 作業: `script_pipeline` / `scripts` の LLM 呼び出し箇所を探索し、`LLMClient` への薄いラッパ関数を用意。タスク名のみを引数にする形へ移行。
  - 検証: 台本生成 E2E を少数サンプルで流し、旧出力との差分（長さ・JSON スキーマ・プロンプト忠実度）をレビュー。
  - ロールバック: ラッパが保持する `use_legacy=false` フラグで旧経路を再有効化。

- **Step 4: 画像パイプラインリプレース**
  - 作業: `ImageClient` と `IMAGE_MODELS.yml` を導入し、Gemini 固定コードを段階的に除去。Remotion/挿絵生成 CLI を全て `ImageClient.generate()` に切替。
  - 検証: 1 本の動画（30〜60 枚）を実生成し、生成失敗率・生成時間・スタイル一貫性を計測。OpenAI/SDK モードで 1 枚ずつテスト生成。
  - ロールバック: `llm_router` の image_gen パスを残し、環境変数で選択。

- **Step 5: 監視とドキュメント**
  - 作業: usage/token/latency を task 単位でメトリクス化し、ダッシュボードを追加。`completed/LLM_LAYER_REFACTOR_PLAN.md` と `IMAGE_MODELS` PLAN を同期更新。
  - 検証: 台本/TTS/画像ごとの月次コスト試算が出せること、失敗率が可視化されていること。
  - ロールバック: メトリクス送信が問題を起こした場合は feature flag で無効化。
- **実装メモ (進捗)**
  - ✅ LLMClient 実装済み（usage ログを `workspaces/logs/llm_usage.jsonl` に JSONL 追記。`LLM_USAGE_LOG_PATH` でパス変更可、`LLM_USAGE_LOG_DISABLE=1` で無効化）。
  - ✅ ImageClient フェイルオーバー実装。commentary 画像経路の legacy router を削除し、ImageClient のみを再試行。
  - ✅ 画像生成ルートは単一: `nanobanana=direct`（ImageClient + Gemini 2.5 flash image）。検証時は `--nanobanana none` を利用。
  - ✅ Gemini image で aspect_ratio を送らない（capabilities supports_aspect_ratio=false）ことで Unknown field エラーを解消。
  - ✅ StyleResolver は `ssot/ops/master_styles.json` が無ければ `config/master_styles.json` をフォールバックする。
  - ✅ abort-on-log オプションを factory/auto_capcut に追加（パターン検知で早期停止、デフォルトは無効）。
  - 推奨 abort-on-log パターン例: `"Unknown field,quota,RESOURCE_EXHAUSTED"`（Gemini画像生成やAPIエラーの早期停止用）。
  - Timeout: デフォルト無指定（無制限）。必要時のみ `--timeout-ms` を指定し、abort-on-log と併用でハング/エラーを安全に止める。
  - ⏳ visual_section_plan の 600 セグ分割スモーク（長尺 SRT でセクション数・境界確認）が未実施。

## 6. 今後の拡張余地
- **コスト可視化**: `LLMResult.usage` をタスク別に集計し、月次ダッシュボード化。
- **動的ルーティング**: 予算・レイテンシ・429 状況に応じて tier を runtime に切替えるオプション。
- **モデル追加手順**: `models` にプロバイダ固有メタを追加→capabilities で制約を宣言→adapter の param_map に反映するだけで差し替え可能にする。

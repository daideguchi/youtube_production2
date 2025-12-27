# OPS_LLM_MODEL_CHEATSHEET — LLMモデル使い分け（正本: configs/llm_router.yaml）

この文書は「どの処理がどのLLMを使うか」を運用目線で固定するためのチートシート。  
**正本は `configs/llm_router.yaml` / `configs/llm_task_overrides.yaml`**（タスク→tier→モデル候補）で、コード側はタスク名で呼び出す。

関連: `ssot/plans/PLAN_LLM_PIPELINE_REFACTOR.md`, `ssot/ops/OPS_LOGGING_MAP.md`

---

## 0. 原則（モジュール管理の“正”）

- **ハードコード禁止**: コードにモデル名（例: `gpt-5-mini`）を直書きしない。
- **タスクキーで呼ぶ**: `LLMClient.call(task=..., messages=...)` の `task` がSoT。
- **切替は設定で**: モデル切替/コスト最適化は `configs/*.yml|*.yaml` 側で完結させる。
- **無断でモデルを追加/切替しない**: `configs/llm*.yml` / `configs/image_models.yaml` の tier 候補やモデルキーを、指示なしで増やしたり順序変更しない（特に画像生成は運用/課金/品質に直結するため厳禁）。例外は SSOT 更新 + 人間レビュー承認後のみ。
- **ログは必ず残す**: `workspaces/logs/llm_usage.jsonl`（集計）に集約し、他の散発ログはL3として短期保持。

---

## 1. 設定の正本（変更レバー）

### 1.1 Primary SoT
- `configs/llm_router.yaml`（および `configs/llm_router.local.yaml`）
  - `providers`: azure/openrouter/gemini の接続情報（env var名）
  - `models`: モデルキー→provider+deployment/model+capabilities
  - `tiers`: tier名→モデルキー候補の優先順
  - `tasks`: タスク名→tier+defaults
- `configs/llm_task_overrides.yaml`
  - task単位の `tier/models/options/system_prompt_override` 上書き（台本パイプラインはここが実質SSOT）

### 1.2 Tier上書き（“このタスクだけ”切替）
- `configs/llm_tier_mapping.yaml`（task→tier）
- `configs/llm_tier_candidates.yaml`（tier→モデル候補）

### 1.3 移行中（参考）
- `configs/llm.yml` / `configs/llm.local.yml`
  - 新 `LLMClient` 系の設定（移行中）。`script_pipeline`（台本生成の主線）は現状こちらを見ない。

---

## 2. 現行のモデル（正本: configs/llm_router.yaml の要約）

### 2.1 テキストLLM（台本/読み/補助）
- `script_pipeline`（台本）は「thinking必須」を固定するため、モデルチェーンを **2つに固定**している。
  - primary: `or_deepseek_v3_2_exp`（OpenRouter / DeepSeek V3.2 Exp）
  - fallback: `or_kimi_k2_thinking`（OpenRouter / MoonshotAI: Kimi K2 Thinking）
- thinking必須の実装（主に“執筆”タスク）:
  - `configs/llm_task_overrides.yaml` で `options.extra_body.reasoning.enabled=true` を付与（章執筆/品質ゲート等）
  - 例外: `script_semantic_alignment_check` は **出力が厳格JSON** のため、OpenRouterの `reasoning` を付けると finish_reason=length で空/破損しやすい。ここは reasoning を付けない（モデル自体の推論は使うが、拡張パラメータは送らない）。
    - さらに、Kimi K2 Thinking は `extra_body.reasoning.enabled` 無しだと空文字になりうるため、このタスクは **DeepSeek固定**（Kimiへフォールバックしない）。
  - 併せて `configs/llm_task_overrides.yaml` で台本系タスクの `max_tokens` を明示（デフォルト依存での空文字/途中切れを避け、コスト暴走も抑える）
  - `packages/factory_common/llm_router.py` が allowlist（V3.2 Exp / Kimi K2 Thinking）にだけ `extra_body.reasoning` を転送する
  - 追加ガード: `packages/factory_common/llm_router.py` は Kimi K2 Thinking に対して `extra_body.reasoning.enabled=true` を自動付与し、空文字事故を避ける（呼び出し側の付け忘れに強くする）。
  - 重要（実挙動）: reasoning を有効にすると completion 側で reasoning tokens を消費するため、`max_tokens` が小さすぎると `finish_reason=length` で **本文が空文字**になることがある（短いチェック系タスクでも起きうる）。  
    対策: `max_tokens` を十分に確保する / `LLM_RETRY_ON_LENGTH=1` を有効にしてリトライで回収する（one-shotでリトライ禁止の箇所は特に注意）。

### 2.2 画像生成（SRT→画像）
- `image_gen` / `image`
  - primary: `gemini_2_5_flash_image`（Gemini / `gemini-2.5-flash-image`）
  - **運用固定**: CapCutドラフト用の画像生成は原則このモデルに固定し、`gemini_3_*` 等へ勝手に切替しない。クォータ/障害時も「黙って別モデルに逃がさず」原因（キー/課金/クォータ）を先に解決する。

---

## 3. タスク→用途（運用での意味）

### 3.1 Script pipeline（台本）
- `script_topic_research`: リサーチ/材料集め（重い推論）
- `script_outline`: 構成（重い推論、thinking高）
- `script_master_plan_opus`（任意）: 設計図（master plan）のサマリを Opus 4.5 で **1回だけ**補強（デフォルトOFF・allowlist必須）
- `script_chapter_brief`: 章の狙い（重い推論、thinking高）
- `script_chapter_draft`: 章ドラフト（重い推論、thinking高）
- `script_chapter_review`: 章レビュー（重い推論、thinking高）
- `script_cta`: CTA/締め（重い推論、thinking高）
- `script_quality_check`: 全体品質チェック（重い推論、thinking高）
- `script_format`: 体裁整形（標準）

### 3.2 TTS（音声）
- `tts_annotate`: 注釈付け（json_object）
- `tts_text_prepare`: 前処理（json_object）
- `tts_segment`: セグメント分割（json_object）
- `tts_pause`: 間の推定（json_object）
- `tts_reading`: 読み解決（重い推論）

### 3.3 Video/Visual（画像文脈・プロンプト）
- `visual_section_plan`: セクション設計
- `visual_persona`: キャラ/一貫性（重い推論）
- `visual_prompt_refine`: プロンプト強化（重い推論）
- `visual_image_gen`: 画像生成（Gemini image）

### 3.4 Belt/Title（補助）
- `belt_generation`: ベルト文言生成（json_object）
- `title_generation`: タイトル生成

---

## 4. 実行時の重要な補足（壊さないための知識）

- `video_pipeline` は実行時に `SRT2IMAGES_IMAGE_MODEL` を上書きして画像モデルを固定する場合がある。
- “remotionは未実装/未運用”のため、現行は CapCut 主線に合わせてタスク/モデルを調整する。

### 4.0 モデル仕様の鮮度確認（OpenRouter）

目的: 「reasoning 等の対応パラメータ」「max_completion_tokens」などは鮮度が命なので、**憶測で決めない**。

- API（最新）: `https://openrouter.ai/api/v1/models`
- ローカル更新（キャッシュ更新）:
  - `./scripts/with_ytm_env.sh python3 packages/script_pipeline/tools/openrouter_models.py`
  - 出力: `packages/script_pipeline/config/openrouter_models.json`

### 4.1 CLI/Env での一時上書き（ルーター改造なしで可変にする）

目的: 実験/比較/コスト最適化のため、**設定ファイルを編集せず**に「この実行だけ」モデルを差し替える。

- 全タスク共通（model chain を固定）:
  - `LLM_FORCE_MODELS="or_deepseek_v3_2_exp,or_kimi_k2_thinking"`（カンマ区切り。モデルキーは `configs/llm_router.yaml: models` のキー）
    - 互換: `deepseek/deepseek-v3.2-exp`（OpenRouter model id）や `gpt-5-mini`（Azure deployment）も **一意に解決できる場合のみ** model key に自動解決される（推奨は常に model key 指定）。
- タスク別（task→model chain）:
  - `LLM_FORCE_TASK_MODELS_JSON='{"script_outline":["or_deepseek_v3_2_exp","or_kimi_k2_thinking"],"tts_annotate":["or_deepseek_v3_2_exp"]}'`
- CLI対応（入口側が上記 env を自動セット）:
  - `python -m script_pipeline.cli run-all --channel CH06 --video 033 --llm-model or_deepseek_v3_2_exp`
  - `PYTHONPATH=".:packages" python -m audio_tts.scripts.run_tts --channel CH06 --video 033 --input ... --llm-model or_deepseek_v3_2_exp`

### 4.2 Azure/非Azure 50/50 ルーティング（運用レバー）

目的: コスト/品質比較のため、同一タスクを Azure とそれ以外で **約半々**に振り分ける。

- 有効化: `LLM_AZURE_SPLIT_RATIO=0.5`
- ルーティングキー: `LLM_ROUTING_KEY`
  - `script_pipeline` は 1エピソード単位で固定になるよう `LLM_ROUTING_KEY={CH}-{NNN}` を自動設定する（同一動画の全ステージが同じ系統になりやすい）。
- 無効化: `LLM_AZURE_SPLIT_RATIO` を未設定（または `0`）

※ 実体ルーター（現行）: `packages/factory_common/llm_router.py`（設定: `configs/llm_router.yaml`）

#### 4.1.1 重要: heavy_reasoning は「台本品質」枠（高コスパ優先）

- `heavy_reasoning` は **台本生成/構成/整合チェック**の主力。まず **OpenRouter の高品質・低コスト枠**（例: DeepSeek V3 系）を優先する。
- OpenAI系の高コスト枠は **コストが跳ねやすい**ため、原則 `heavy_reasoning` には入れない（入れる場合もコメントアウトで温存し、必要時だけ有効化する）。

### 4.3 「どのLLMが書いたか」を確実に残す（証跡）

- 正本: `workspaces/scripts/{CH}/{NNN}/status.json`
  - `stages.*.details.llm_calls[]` に provider/model/request_id/chain などを記録する。
- 参照用マニフェスト: `workspaces/scripts/{CH}/{NNN}/script_manifest.json`
  - status の内容と、`artifacts/llm/*.json` を同梱して追跡できるようにする。

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
  - `providers`: azure/openrouter/fireworks/gemini の接続情報（env var名）
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
- `script_pipeline`（台本）の **“本文執筆/品質審査/意味整合”** は「thinking必須」を固定するため、モデルチェーンを **2つに固定**する（原則）。
  - primary: `or_deepseek_v3_2_exp`（Fireworks / DeepSeek V3.2）
  - fallback: `or_kimi_k2_thinking`（OpenRouter / MoonshotAI: Kimi K2 Thinking）

#### 2.1.1 Decision（2025-12-28）: 台本系モデルは「2つ」に固定（DeepSeek v3.2 exp / Kimi K2 Thinking）
対象（正本）:
- `configs/llm_router.yaml`
- `configs/llm_task_overrides.yaml`

結論:
- `script_pipeline`（台本）の **“本文執筆/品質審査/字数救済/最終磨き込み”** は原則この2つだけを使う。
  - primary: `or_deepseek_v3_2_exp`
  - fallback: `or_kimi_k2_thinking`

理由（品質×コスト）:
- 執筆は thinking 必須（指示）。モデルチェーンを増やすと「文体ぶれ/契約ぶれ/収束不安定」を増やし、結果的にコストが上がる。
- Kimi K2 Thinking（OpenRouter）は `extra_body.reasoning.enabled` が無いと空文字になり得るため、`packages/factory_common/llm_router.py` が自動付与でガードしている（呼び出し側の付け忘れ耐性）。
- DeepSeek v3.2（Fireworks）は `reasoning_effort` で思考ONできるが、思考文が `content` に混ざりやすい。repo 既定の `exclude: true` 指定は「思考を無効化」ではなく「出力から除外」を意味し、`packages/factory_common/llm_router.py` が Fireworks では最終出力だけを抽出して返す（非JSON: マーカー `<<<YTM_FINAL>>>` 以降を採用 / JSON: `{...}` だけを抽出）。

運用ルール（重要: 憶測でパラメータを決めない）:
- モデル仕様（reasoning対応/上限等）は鮮度が命。必要なら `https://openrouter.ai/api/v1/models` を参照し、ローカルでは `packages/script_pipeline/config/openrouter_models.json` を更新してから調整する。
- thinking必須の実装（主に“執筆/審査”タスク）:
  - `configs/llm_task_overrides.yaml` で `options.extra_body.reasoning.enabled=true` を付与（章執筆/品質ゲート等）
  - `script_semantic_alignment_check` は **出力が厳格JSON** のため、`response_format: json_object` を使って「思考ありでもJSONが壊れにくい」側に寄せる（DeepSeek固定 / Kimiへフォールバックしない）。

### 2.2 画像生成（サムネ / 動画内画像）

正本（重要）:
- `configs/image_models.yaml`（ImageClient: task→tier→model）
- サムネ: `workspaces/thumbnails/templates.json: channels[].templates[].image_model_key`
- 動画内画像（SRT→画像）: `packages/video_pipeline/config/channel_presets.json: channels.<CH>.image_generation.model_key`
  - 実行時は `packages/video_pipeline/src/srt2images/orchestration/pipeline.py` が `IMAGE_CLIENT_FORCE_MODEL_KEY_VISUAL_IMAGE_GEN` を自動セットする。

現行の既定（2025-12-31）:
- サムネ（背景生成）: `fireworks_flux_kontext_max`（FLUX.1 Kontext Max）
- 動画内画像（CapCutドラフト用）:
  - CH01: `fireworks_flux_kontext_max`
  - CH02 / CH05 / CH22 / CH23: `fireworks_flux_kontext_pro`
  - その他: tier既定 `fireworks_flux_1_schnell_fp8`（FLUX.1 schnell）

運用ルール（重要）:
- 無断で tier 候補を増やしたり順序変更しない（ImageClientのround-robinで“既定”がズレるため）。
- 「一貫性」: 画像プロンプトのガードレール +（Kontextは）参照画像（input_image）で人物/場面のブレを抑える。
- 一時切替（ファイル編集なし / その実行だけ）:
  - 動画内画像: `IMAGE_CLIENT_FORCE_MODEL_KEY_VISUAL_IMAGE_GEN=openrouter_gemini_2_5_flash_image`
  - サムネ: `IMAGE_CLIENT_FORCE_MODEL_KEY_THUMBNAIL_IMAGE_GEN=openrouter_gemini_2_5_flash_image`

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
- `script_a_text_quality_judge` / `script_a_text_quality_fix`: `script_validation` の QC（judge→fix）。fix は安定性優先で Kimi を先に試す（コスト増だが「途中で切れる/部分出力」事故を減らす）。

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
- `visual_image_gen`: 画像生成（ImageClient。正本: `configs/image_models.yaml` / CH別固定: `packages/video_pipeline/config/channel_presets.json`）

### 3.4 Belt/Title（補助）
- `belt_generation`: ベルト文言生成（json_object）
- `title_generation`: タイトル生成

---

## 4. 実行時の重要な補足（壊さないための知識）

- `video_pipeline` は実行時に `IMAGE_CLIENT_FORCE_MODEL_KEY_VISUAL_IMAGE_GEN` をセットして画像モデルを固定する（チャンネルpreset由来）。
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

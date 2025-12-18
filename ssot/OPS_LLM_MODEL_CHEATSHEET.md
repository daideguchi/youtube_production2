# OPS_LLM_MODEL_CHEATSHEET — LLMモデル使い分け（正本: configs/llm.yml）

この文書は「どの処理がどのLLMを使うか」を運用目線で固定するためのチートシート。  
**正本は `configs/llm.yml`**（タスク→tier→モデル候補）で、コード側はタスク名で呼び出す。

関連: `ssot/PLAN_LLM_PIPELINE_REFACTOR.md`, `ssot/OPS_LOGGING_MAP.md`

---

## 0. 原則（モジュール管理の“正”）

- **ハードコード禁止**: コードにモデル名（例: `gpt-5-mini`）を直書きしない。
- **タスクキーで呼ぶ**: `LLMClient.call(task=..., messages=...)` の `task` がSoT。
- **切替は設定で**: モデル切替/コスト最適化は `configs/*.yml|*.yaml` 側で完結させる。
- **無断でモデルを追加/切替しない**: `configs/llm*.yml` / `configs/image_models.yaml` の tier 候補やモデルキーを、指示なしで増やしたり順序変更しない（特に画像生成は運用/課金/品質に直結するため厳禁）。例外は SSOT 更新 + 人間レビュー承認後のみ。
- **ログは必ず残す**: `workspaces/logs/llm_usage.jsonl`（集計。互換: `logs/llm_usage.jsonl`）に集約し、他の散発ログはL3として短期保持。

---

## 1. 設定の正本（変更レバー）

### 1.1 Primary SoT
- `configs/llm.yml`
  - `providers`: azure/openrouter/gemini の接続情報（env var名）
  - `models`: モデルキー→provider+deployment/model+capabilities
  - `tiers`: tier名→モデルキー候補の優先順
  - `tasks`: タスク名→tier+defaults

### 1.2 Tier上書き（“このタスクだけ”切替）
- `configs/llm_tier_mapping.yaml`（task→tier）
- `configs/llm_tier_candidates.yaml`（tier→モデル候補）

### 1.3 Legacy（移行中）
- `configs/llm_router.yaml`, `configs/llm_task_overrides.yaml`, `configs/llm_registry.json`
  - 新 `LLMClient` の互換目的で読み取りされる場合があるため、削除/移動は計画化して段階実行する。

---

## 2. 現行のモデル（configs/llm.yml の要約）

### 2.1 テキストLLM（台本/読み/補助）
- `heavy_reasoning` / `standard` / `cheap`
  - primary: `azure_gpt5_mini`（Azure / Responses API / deployment `gpt-5-mini`）

### 2.2 画像生成（SRT→画像）
- `image_gen` / `image`
  - primary: `gemini_2_5_flash_image`（Gemini / `gemini-2.5-flash-image`）
  - **運用固定**: CapCutドラフト用の画像生成は原則このモデルに固定し、`gemini_3_*` 等へ勝手に切替しない。クォータ/障害時も「黙って別モデルに逃がさず」原因（キー/課金/クォータ）を先に解決する。

---

## 3. タスク→用途（運用での意味）

### 3.1 Script pipeline（台本）
- `script_topic_research`: リサーチ/材料集め（重い推論）
- `script_outline`: 構成（重い推論、thinking高）
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

- `commentary_02_srt2images_timeline` は実行時に `SRT2IMAGES_IMAGE_MODEL` を上書きして画像モデルを固定する場合がある。
- “remotionは未実装/未運用”のため、現行は CapCut 主線に合わせてタスク/モデルを調整する。

### 4.0 CLI/Env での一時上書き（ルーター改造なしで可変にする）

目的: 実験/比較/コスト最適化のため、**設定ファイルを編集せず**に「この実行だけ」モデルを差し替える。

- 全タスク共通（model chain を固定）:
  - `LLM_FORCE_MODELS="or_deepseek_r1_0528,azure_gpt5_mini"`（カンマ区切り。モデルキーは `configs/llm_router.yaml: models` のキー）
- タスク別（task→model chain）:
  - `LLM_FORCE_TASK_MODELS_JSON='{"script_outline":["or_deepseek_r1_0528"],"tts_annotate":["or_deepseek_v3_2_exp"]}'`
- CLI対応（入口側が上記 env を自動セット）:
  - `python -m script_pipeline.cli run-all --channel CH06 --video 033 --llm-model or_deepseek_r1_0528`
  - `PYTHONPATH=".:packages" python -m audio_tts_v2.scripts.run_tts --channel CH06 --video 033 --input ... --llm-model or_deepseek_v3_2_exp`

### 4.1 Azure/非Azure 50/50 ルーティング（運用レバー）

目的: コスト/品質比較のため、同一タスクを Azure とそれ以外で **約半々**に振り分ける。

- 有効化: `LLM_AZURE_SPLIT_RATIO=0.5`
- ルーティングキー: `LLM_ROUTING_KEY`
  - `script_pipeline` は 1エピソード単位で固定になるよう `LLM_ROUTING_KEY={CH}-{NNN}` を自動設定する（同一動画の全ステージが同じ系統になりやすい）。
- 無効化: `LLM_AZURE_SPLIT_RATIO` を未設定（または `0`）

※ 実体ルーター（現行）: `packages/factory_common/llm_router.py`（設定: `configs/llm_router.yaml`）

#### 4.1.1 重要: heavy_reasoning は「台本品質」枠（高コスパ優先）

- `heavy_reasoning` は **台本生成/構成/整合チェック**の主力。まず **OpenRouter の高品質・低コスト枠**（例: DeepSeek V3 系）を優先する。
- OpenAI系の高コスト枠は **コストが跳ねやすい**ため、原則 `heavy_reasoning` には入れない（入れる場合もコメントアウトで温存し、必要時だけ有効化する）。

### 4.2 「どのLLMが書いたか」を確実に残す（証跡）

- 正本: `workspaces/scripts/{CH}/{NNN}/status.json`
  - `stages.*.details.llm_calls[]` に provider/model/request_id/chain などを記録する。
- 参照用マニフェスト: `workspaces/scripts/{CH}/{NNN}/script_manifest.json`
  - status の内容と、`artifacts/llm/*.json` を同梱して追跡できるようにする。

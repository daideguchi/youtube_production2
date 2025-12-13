# OPS_LLM_MODEL_CHEATSHEET — LLMモデル使い分け（正本: configs/llm.yml）

この文書は「どの処理がどのLLMを使うか」を運用目線で固定するためのチートシート。  
**正本は `configs/llm.yml`**（タスク→tier→モデル候補）で、コード側はタスク名で呼び出す。

関連: `ssot/PLAN_LLM_PIPELINE_REFACTOR.md`, `ssot/OPS_LOGGING_MAP.md`

---

## 0. 原則（モジュール管理の“正”）

- **ハードコード禁止**: コードにモデル名（例: `gpt-5-mini`）を直書きしない。
- **タスクキーで呼ぶ**: `LLMClient.call(task=..., messages=...)` の `task` がSoT。
- **切替は設定で**: モデル切替/コスト最適化は `configs/*.yml|*.yaml` 側で完結させる。
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

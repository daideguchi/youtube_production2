# LLM パイプラインリファクタ計画

> Plan metadata
> - Plan ID: **PLAN_LLM_PIPELINE_REFACTOR**
> - ステータス: Active
> - 担当/レビュー: Codex（SSOT メンテナ）
> - 対象範囲 (In Scope): `script_pipeline`, `audio_tts_v2`, `commentary_02_srt2images_timeline` の LLM 呼び出し経路と共通ルーター
> - 非対象 (Out of Scope): UI/投稿自動化、Remotion/CapCut テンプレの詳細設計
> - 最終更新日: 2025-12-10

## 1. 概要
- **目的**: 台本（Aテキスト）、TTS 用 Bテキスト、画像ドラフトの3系統を横断し、LLM 呼び出しの品質・一貫性・耐障害性を高める。
- **対象範囲**: script_pipeline（台本生成・整形）、audio_tts_v2（Bテキスト・音声）、commentary_02_srt2images_timeline（画像チャンク解析・プロンプト）、共通 LLM 呼び出し層。

## 2. 現状把握
### 2.1 LLM 呼び出し一覧
| file_path | function / class | provider | model指定/特徴 | 用途 | 主なパラメータ |
| --- | --- | --- | --- | --- | --- |
| `script_pipeline/runner.py` | `_call_azure_chat` | Azure | deployment + Responses/Chat API 切替 | 台本各ステージ（outline/draft/format 等） | `max_completion_tokens/max_output_tokens`, `reasoning_effort`, optional `response_format`, timeout 240s【F:script_pipeline/runner.py†L345-L463】 |
| `script_pipeline/runner.py` | `_call_openrouter_chat` | OpenRouter | 任意 model ID | 台本ステージ fallback/指定 | `max_tokens`, messages or prompt, timeout 240s【F:script_pipeline/runner.py†L465-L508】 |
| `script_pipeline/runner.py` | `_call_gemini_generate` | Gemini | `generateContent` v1beta | 台本ステージ（特に draft/format） | `maxOutputTokens`, optional `thinkingLevel`, JSON messages, timeout 120–240s【F:script_pipeline/runner.py†L510-L619】 |
| `audio_tts_v2/tts/llm_adapter.py` | `annotate_tokens` / `segment_text_llm` / `suggest_pauses` / `tts_text_prepare` | Router経由 (Azure/Gemini/OpenRouter) | `configs/llm.yml` | TTS 注釈・分割・pause推定・Bテキスト準備 | `max_output_tokens/max_tokens`, `response_format=json_object`, `timeout` などをタスク定義で正規化【F:audio_tts_v2/tts/llm_adapter.py†L1-L420】 |
| `audio_tts_v2/tts/llm_adapter.py` | `annotate_tokens`, `segment_text_llm`, `suggest_pauses`, `B_TEXT_GEN_PROMPT` | Azure (default) | system/user プロンプト直書き | 読み誤り検出、SRT 分割、ポーズ付与、Bテキスト生成 | JSON schema 指定、最大 3000 tokens、失敗時デフォルト fallback【F:audio_tts_v2/tts/llm_adapter.py†L12-L218】 |
| `commentary_02_srt2images_timeline/src/srt2images/llm_context_analyzer.py` | `LLMContextAnalyzer` | Gemini / Azure / OpenRouter | registry + env で切替 | SRT セクション解析（画像用文脈） | Azure: retries/backoff, max_completion_tokens=60000; OpenRouter: 4096 tokens; Gemini: `genai.Client`【F:commentary_02_srt2images_timeline/src/srt2images/llm_context_analyzer.py†L37-L214】 |

### 2.2 台本生成フロー（企画CSV → Aテキスト）
- `script_pipeline/runner.py` が `stages.yaml` 定義を読み込み、各ステージを `_run_llm` でテンプレート + プレースホルダ置換し LLM へ投げる。テンプレートは `templates.yaml` とファイル参照。出力は `script_pipeline/data/<channel>/<video>/content/` 配下に章ごと (`chapters/`)・整形済み (`chapters_formatted/`) などで保存。【F:script_pipeline/runner.py†L1213-L1350】
- 台本ドラフト生成は章単位で `outline.md` から章タイトルを読み、章ごとに LLM 呼び出しを行う。word target はメタデータやチャンネルで自動配分。【F:script_pipeline/runner.py†L1673-L1710】
- 整形ステージ (`script_draft_format`) はチャンクに分割し、29〜35文字制限を LLM で再改行。失敗時フォールバックや警告記録のみで進行。【F:script_pipeline/runner.py†L1347-L1400】【F:script_pipeline/runner.py†L1504-L1573】

### 2.3 TTS（Bテキスト）と音声生成フロー
- `audio_tts_v2/tts/orchestrator.py` で Aテキストを前処理→MeCab トークン化→かなエンジン構築→SRT ブロック生成。見出し検証・グループ付与後にドラフト読み（MeCab）を付与。【F:audio_tts_v2/tts/orchestrator.py†L138-L260】
- LLM は `llm_adapter` 経由で「危険トークン注釈」「セグメント分割」「ポーズ推定」等を行うが、Bテキスト生成（待ちタグ含む）は単一プロンプト `B_TEXT_GEN_PROMPT` で一括生成し、構造的な三段階化は未導入。【F:audio_tts_v2/tts/llm_adapter.py†L81-L218】
- QA/annotation は Router 経由。失敗時はデフォルトのカナを返してパイプライン継続する設計（`llm_adapter` 内でフォールバック実装）。

### 2.4 画像用文脈解析・プロンプト生成
- `commentary_02_srt2images_timeline/src/srt2images/cue_maker.py` で SRT セグメントを LLM 文脈解析に渡し、自然なセクション境界と summary/visual_focus を得る。モデルは `LLMContextAnalyzer` が registry/env から選択（Gemini/ Azure / OpenRouter）。【F:commentary_02_srt2images_timeline/src/srt2images/cue_maker.py†L13-L112】
- `LLMContextAnalyzer` は Azure 429/5xx にバックオフリトライ、OpenRouter も REST 呼び出し、Gemini 3 系をデフォルトに採用。セクション情報は cue に書き戻され、後続の画像生成（Gemini image 等）に利用されるが、キャラ・世界観の一貫性を守る「Visual Bible」挿入は未実装。【F:commentary_02_srt2images_timeline/src/srt2images/llm_context_analyzer.py†L37-L214】

### 2.5 問題点
- LLM 呼び出し口が複数（script_pipeline 独自、audio_tts_v2 独自、srt2images 独自）で、model registry やパラメータ互換の統一がない。
- 台本生成は章ごとドラフトと整形はあるが、アウトライン生成・章ごとの自己レビュー・全体整合チェックが欠落し、一発生成寄り。
- Bテキスト生成は単一プロンプトで誤読防止・ポーズ制御を同時に行っており、MeCab + LLM による三段階化が未導入。
- 画像側はセクション解析のみ LLM に依存し、スタイル統一（Visual Bible）や重厚モデルでのプロンプト整備が不足。
- エラーハンドリングは場所により差があり、script_pipeline では Gemini 429 fallback→Azure ありだが OpenRouter/Azure 失敗時の再送・tier 降格は未統一。【F:script_pipeline/runner.py†L1370-L1400】

## 3. 目指すアーキテクチャ
### 3.1 共通 LLM ルーター
- 単一の `llm_router.call(task=..., messages=[...], options=...)` を導入し、全コンポーネントが task 名で呼ぶ。
- **タスク→tier→model** マッピング（例 YAML）:
```yaml
tiers:
  heavy_reasoning: [openrouter:deepseek-v3.2-exp, openrouter:deepseek-r1-distill-qwen-32b, azure:gpt-5-mini]
  standard: [openrouter:deepseek-v3.2-exp, openrouter:tencent/hunyuan-a13b-instruct, openrouter:qwen-2.5-7b-instruct]
  cheap: [openrouter:google/gemma-3n-e2b-it:free, openrouter:mistralai/mistral-7b-instruct:free]
  vision_caption: [openrouter:qwen2.5-vl-72b-instruct]
  image: [gemini:2.5-flash-image]

tasks:
  script_outline: {tier: heavy_reasoning, failure_policy: hard_fail}
  script_chapter_draft: {tier: heavy_reasoning, failure_policy: hard_fail}
  script_chapter_review: {tier: heavy_reasoning, failure_policy: soft_fallback}
  tts_text_prepare: {tier: standard, failure_policy: soft_fallback}
  tts_pause_suggestion: {tier: standard, failure_policy: soft_fallback}
  visual_section_plan: {tier: heavy_reasoning, failure_policy: soft_fallback}
  visual_prompt_from_chunk: {tier: heavy_reasoning, failure_policy: soft_fallback}
```

### 3.2 モデル capabilities/パラメータ互換
- `models` 定義に `provider`, `api_type (chat/responses/image)`, `allow_reasoning`, `allow_temperature`, `allow_stop`, `capabilities (json_mode/tools/vision/streaming/max_completion_tokens naming)` を持たせる。
- ルーターで不正パラメータを自動削除・変換（例: Gemini で `max_output_tokens` → `maxOutputTokens`、Azure Chat では `response_format` を無視）。
- パラメータ互換の例:
```yaml
models:
  azure_gpt5m:
    provider: azure
    api_type: responses
    deployment: gpt-5-mini
    allow_reasoning: true
    capabilities: {json_mode: false, max_completion_tokens: 16000}
  gemini_flash:
    provider: gemini
    api_type: responses
    model: gemini-2.5-flash
    capabilities: {json_mode: true, vision: true, max_output_tokens: 8000}
```

### 3.3 エラーハンドリングと fallback
- `failure_policy`: `hard_fail` は tier 内モデルを尽くしたら停止、`soft_fallback` は tier を順次降格（heavy→standard→cheap）。
- `retry_policy`: ReadTimeout/429 は指数バックオフで 2–3 回まで再送。provider 固有のステータスをマッピングし、idempotent な場合のみ再送。
- 429/QUOTA で自動的に fallback モデルを `force_fallback` として短時間キャッシュする（現行 script_pipeline の挙動を一般化）。

## 4. 台本構築ロジックの改善案
### 4.1 階層アウトライン生成 (`script_outline`)
- **入力**: 企画 CSV 1 行（video_id, タイトル, キーワード, 尺, ペルソナなど）。
- **出力**: `script_plan.json` (例 `script_pipeline/data/CHxx/NNN/script_plan.json`) に `{global_summary, chapters:[{title, target_words, bullets[...] }]}` を保存。
- **実装ポイント**: `script_pipeline/runner.py` に新ステージ `script_outline` を追加し、テンプレートを `templates/outline_plan.txt` として `llm_router.call(task="script_outline")` で生成。`_load_csv_row` で得たメタデータを placeholders に渡す。【F:script_pipeline/runner.py†L580-L664】

### 4.2 章ごとの本文生成 (`script_chapter_draft`)
- `script_plan.json` を読み、`global_summary` と `previous_chapters_summary` を system に渡しつつ章ごとに LLM 呼び出し。既存の章ごと生成ループ（`_parse_outline_chapters` 〜 `_run_llm`）を差し替え、出力を `chapters/chapter_<n>.md` に保存。【F:script_pipeline/runner.py†L1647-L1710】
- 互換性: 旧テンプレートを `legacy_*` として残し、ステージ設定で新旧を切替。移行期間中は `SCRIPT_PIPELINE_USE_PLAN=1` で新フローを有効化。

### 4.3 章ごとの自己レビュー＆リライト (`script_chapter_review`)
- プロンプト: 章本文 + checklist（論理破綻/トーン/TTS 読みやすさ）。LLM に `{issues:[...], revised_text:"..."}` を JSON で返させる。
- 保存: `content/chapters_reviewed/chapter_<n>.json` に issues + revised を格納し、採用時のみ `chapters_formatted/` に書き戻す。

### 4.4 全体整合チェック (`script_global_consistency`)
- 入力: `script_plan.json` + 全章本文。LLM に欠落・重複ポイントを列挙させ、追記案を `consistency_report.json` として保存。必要なら `chapter_appendix.md` を生成し組み込み。

## 5. TTS 用 Bテキストと画像ドラフトの改善案
### 5.1 Bテキスト生成パイプライン
1. **分割**: MeCab + 見出し維持で文・文節を区切り (`_raw_sentence_blocks_for_srt` 既存)。【F:audio_tts_v2/tts/orchestrator.py†L214-L245】
2. **誤読リスク判定**: `annotate_tokens` を `task=tts_annotate` として標準 tier の JSON モードで呼び、危険トークンを抽出。【F:audio_tts_v2/tts/llm_adapter.py†L103-L218】
3. **読みやすさ整形**: 新タスク `tts_text_prepare` で Aテキストに対し、「読み替え」「ゆっくりタグ」だけを返す JSON `{segments:[{text, reading_hint, pause_sec}]}` を生成。`B_TEXT_GEN_PROMPT` はこの出力を組み立てる最終ステップとして分離。
4. **SSML/ポーズ挿入**: 生成した pause 情報から `[wait=...]` または `<break time="..."/>` を付与し、`builder.py` で Bテキストを構成。
5. **統合**: 既存の `generate_draft_readings` → `audit_blocks` の前に挿入し、LLM 出力が空なら従来の MeCab ドラフトにフォールバック。

### 5.2 画像ドラフト強化
- **Visual Bible**: `commentary_02_srt2images_timeline/data/visual_bible.json`（新設）にキャラ設定・色味・カメラワークを保存。`LLMContextAnalyzer` のプロンプトに `visual_bible` を system として注入。
- **セクション計画 (`visual_section_plan`)**: `analyze_story_sections` を heavy_reasoning tier で再実行し、セクションごとに `visual_focus`/`persona_needed` を強化。
- **チャンクプロンプト生成 (`visual_prompt_from_chunk`)**: 各 cue を heavy_reasoning モデルに渡し、Visual Bible + 直前/直後セクション文脈を含むプロンプトを生成。既存の機械的 summary を置換、Gemini Image へ渡す下準備を統一。

## 6. 具体的なリファクタ案
- **コンフィグ**: `configs/llm_router.yaml` に tiers/models/tasks を集約。既存 `llm_model_registry.yaml` はモデル定義のみを残し、router が読み込む。
- **共通クライアント**: `factory_common/llm_router.py`（新規）で `call(task, messages|prompt, media=None, options={})` を提供。provider ごとに adapter を実装し、パラメータ正規化とログを統一。
- **パイプライン改修**:
  - `script_pipeline/runner.py` 内の `_run_llm` 呼び出しを router 経由に差し替え、stage 定義に `task` を記述。
  - `audio_tts_v2/tts/llm_adapter.py` の直接 `azure_chat_with_fallback` 呼び出しを router に置換し、Bテキスト三段階化を追加。
  - `commentary_02_srt2images_timeline` の `LLMContextAnalyzer` を router を使う薄いラッパーに変更し、Visual Bible を system prompt に注入。
- **段階的マイグレーション**:
  1. Router + config 追加（既存 API を包む互換レイヤー）。
  2. script_pipeline を router 呼び出しに移行（stage 定義に task を追記）。
  3. TTS 三段階化と router 統合、既存 fallback を維持。
  4. 画像セクション/プロンプト生成を router 化し、Visual Bible を導入。
  5. 旧直接呼び出しを段階的に削除し、テストを整備。

## 7. 実装タスク一覧（ToDo）
- [x] `configs/llm_router.yaml` 作成（tiers/models/tasks 定義）。  
  - 追加済み: tier→model 候補、task→tier、fallback ポリシー、タスクオーバーライド。
- [x] `factory_common/llm_router.py` 実装（provider adapter, capability 正規化, retry/fallback）。  
  - 追加済み: llm_param_guard, per-status backoff, usage JSONL ログ。
- [ ] `script_pipeline` ステージ定義更新（outline/plan/review/consistency 追加、task 名付与）。  
  - 現状: 旧 `_run_llm` 直接呼び出しのまま。stage 定義の task 化・router 経由未導入。
- [ ] `script_pipeline/runner.py` を router API に差し替え、`script_plan.json` 保存/読込導線追加。  
  - 現状: 章ドラフト/整形は直接 Azure/Gemini 呼び出し。outline/review/consistency 未実装。
- [ ] `audio_tts_v2/tts/llm_adapter.py` に三段階 Bテキスト生成ロジックと router 呼び出しを導入、`builder.py` で SSML 生成を統合。  
  - 進捗: llm_adapter は router 呼び出しに統一済み。`generate_reading_script` は segment→reading の二段階に再構成済み。`generate_reading_for_blocks` も router 一括呼び出し化。`tts_text_prepare` の導線は orchestrator/builder まで配線済み（pause/ruby適用）。SSML側に `<break>` を入れる追加実装が必要なら残タスク。
- [ ] `audio_tts_v2` 内の古い参照 (`auditor.py`, `qa_adapter.py`, `arbiter.py`, `strict_orchestrator.py`) を全て `LLMRouter` に移行し、旧ドキュメント言及を掃除する。  
  - 現状: llm_adapter は移行完了。旧 `llm_client.py` は削除済み/非参照。残るのは文書の片付けと、SSMLへの `<break>` 挿入を要する場合の仕上げ。
- [ ] `commentary_02_srt2images_timeline` に Visual Bible 読込と router 呼び出しを追加、画像プロンプト生成部を差し替え。  
  - 現状: Visual Bible 未読込。Gemini image 直呼び出し＆テンプレ混在。
- [ ] 回帰テスト・サンプルパイプライン（script→tts→image）の E2E テストを追加。  
  - 現状: 断片的な単体テストのみ。end-to-end 未整備。

### 次に着手すべき具体ステップ（優先度順）
1) script_pipeline: stages.yaml/templates.yaml に task を明示し、`_run_llm` で `resolve_task` を必須にする（fallback 直指定禁止）。  
2) script_pipeline: `script_outline`/`script_chapter_review`/`script_global_consistency` を新設（空でもよい）し、ルータ経由で実行できるようにする。  
3) TTS: `tts_annotate`→`tts_text_prepare`→`tts_reading` の三段階を導線化し、旧 `llm_client` 参照を全削除。  
4) commentary_02: `LLMContextAnalyzer` と image prompt 生成を router 経由に統一し、nanobanana=direct 以外の分岐を削除。  
5) E2E: script→tts→image の最小ケースを `tests/test_e2e_pipeline.py` などで追加し、環境変数で実行可否を制御。  

## 8. 付録
- **主要関数**
  - script_pipeline: `_call_azure_chat`, `_call_openrouter_chat`, `_call_gemini_generate`, `_run_llm`（ステージ共通 LLＭ 呼び出し）、章生成ループ。【F:script_pipeline/runner.py†L345-L463】【F:script_pipeline/runner.py†L465-L508】【F:script_pipeline/runner.py†L510-L619】【F:script_pipeline/runner.py†L1213-L1350】
  - TTS: `azure_responses` / `gemini_generate_content`（LLM クライアント）, `annotate_tokens`/`B_TEXT_GEN_PROMPT`（読み・Bテキスト変換）, `run_tts_pipeline`（前処理～ドラフト）。【F:audio_tts_v2/tts/llm_client.py†L84-L180】【F:audio_tts_v2/tts/llm_adapter.py†L81-L218】【F:audio_tts_v2/tts/orchestrator.py†L138-L260】
  - 画像: `LLMContextAnalyzer` + `cue_maker.make_cues`（セクション解析）。【F:commentary_02_srt2images_timeline/src/srt2images/llm_context_analyzer.py†L37-L214】【F:commentary_02_srt2images_timeline/src/srt2images/cue_maker.py†L13-L112】
- **簡易フローチャート（テキスト）**
  - **Script**: CSV/metadata → outline plan → chapter drafts (LLM) → format (LLM) → assembled Aテキスト.
  - **TTS**: Aテキスト → mech split → risk annotate (LLM) → Bテキスト生成 (LLM) → pause insert → synthesis.
  - **Image**: SRT → LLM section analysis → cues with summaries/visual_focus → prompt draft → image generation.

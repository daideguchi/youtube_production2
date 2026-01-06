# OPS_LLM_MODEL_CHEATSHEET — LLMモデル使い分け（正本: configs/llm_router.yaml）

この文書は「どの処理がどのLLMを使うか」を運用目線で固定するためのチートシート。  
**正本は `configs/llm_router.yaml` / `configs/llm_task_overrides.yaml`**（タスク→tier→モデル候補）。コード側は task 名で呼び出し、運用は **モデルコード/数字スロット**で切替える（モデル名直書き禁止）。

関連: `ssot/ops/OPS_CHANNEL_MODEL_ROUTING.md`, `ssot/plans/PLAN_LLM_PIPELINE_REFACTOR.md`, `ssot/ops/OPS_LOGGING_MAP.md`

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
  - 注意: `configs/llm_router.local.yaml` は `configs/llm_router.yaml` への **deep merge overlay**（差分だけ書く）。local に無い task/tier を消さない（実装: `packages/factory_common/llm_router.py`）。
  - `providers`: azure/openrouter/fireworks/gemini の接続情報（env var名）
  - `models`: モデルキー→provider+deployment/model+capabilities
  - `tiers`: tier名→モデルキー候補の優先順
  - `tasks`: タスク名→tier+defaults
- `configs/llm_task_overrides.yaml`
  - task単位の `tier/models/options/system_prompt_override` 上書き（台本パイプラインはここが実質SSOT）
- `configs/llm_model_codes.yaml`
  - 運用で使う **モデルコード**（例: `script-main-1` / `open-kimi-thinking-1`）→ `llm_router.yaml: models.<model_key>` に解決
- `configs/llm_model_slots.yaml`
  - **数字スロット** `LLM_MODEL_SLOT`（tier→モデルコード）でブレなく切替える（モデル名を書き換えない）
- `configs/llm_exec_slots.yaml`
  - **数字スロット** `LLM_EXEC_SLOT`（api/think/agent/codex exec/failover）で「どこで動くか」を固定

### 1.2 Legacy（原則使わない）: Tier上書き
この repo では運用切替は **`LLM_MODEL_SLOT`** に統一しているため、ここは原則使わない（混乱/ズレ防止）。

- `configs/llm_tier_mapping.yaml`（task→tier; legacy）
- `configs/llm_tier_candidates.yaml`（tier→モデル候補; legacy）

### 1.3 移行中（参考）
- `configs/llm.yml` / `configs/llm.local.yml`
  - 新 `LLMClient` 系の設定（移行中）。`script_pipeline`（台本生成の主線）は現状こちらを見ない。

---

## 2. 現行のモデル（正本: configs/llm_router.yaml の要約）

### 2.1 テキストLLM（台本/読み/補助）
- `script_pipeline`（台本）の **“本文執筆/品質審査/意味整合”** は「thinking必須」を固定するため、既定のモデル選択を **最小**に保つ（原則）。
  - primary: `fw-d-1`（Fireworks / DeepSeek V3.2 exp）
  - optional fallback（比較/非常時; opt-in）: `open-k-1`（OpenRouter / Kimi K2 Thinking。`script_*` で OpenRouter を候補に入れるには `YTM_SCRIPT_ALLOW_OPENROUTER=1` または slot 側 `script_allow_openrouter: true` が必要）
  - 比較用（既定では使わない）: `fw-g-1`, `fw-m-1`（Fireworks。通常運用は slot 切替。`LLM_FORCE_MODELS`/`--llm-model` は緊急デバッグのみで `YTM_EMERGENCY_OVERRIDE=1` が必要）

#### 2.1.1 Decision（2025-12-28）: 台本系モデルは「2つ」に固定（DeepSeek v3.2 exp / Kimi K2 Thinking）
対象（正本）:
- `configs/llm_router.yaml`
- `configs/llm_task_overrides.yaml`

結論:
- `script_pipeline`（台本）の **“本文執筆/品質審査/字数救済/最終磨き込み”** は原則この2つだけを使う。
  - primary: `fw-d-1`
  - fallback: `open-k-1`
  - 例外（比較/検証）: `fw-g-1`, `fw-m-1` 等は **runtime override 時のみ**（既定のチェーンは増やさない）

理由（品質×コスト）:
- 執筆は thinking 必須（指示）。モデルチェーンを増やすと「文体ぶれ/契約ぶれ/収束不安定」を増やし、結果的にコストが上がる。
- Kimi K2 Thinking（OpenRouter）は `extra_body.reasoning.enabled` が無いと空文字になり得るため、`packages/factory_common/llm_router.py` が自動付与でガードしている（呼び出し側の付け忘れ耐性）。
- DeepSeek v3.2（Fireworks）は `reasoning_effort` で思考ONできるが、思考文が `content` に混ざりやすい。repo 既定の `exclude: true` 指定は「思考を無効化」ではなく「出力から除外」を意味し、`packages/factory_common/llm_router.py` が Fireworks では最終出力だけを抽出して返す（非JSON: マーカー `<<<YTM_FINAL>>>` 以降を採用 / JSON: `{...}` だけを抽出）。

運用ルール（重要: 憶測でパラメータを決めない）:
- モデル仕様（reasoning対応/上限等）は鮮度が命。必要なら以下で **実在と仕様**を確認してから調整する。
  - OpenRouter: `https://openrouter.ai/api/v1/models`（ローカル更新: `packages/script_pipeline/config/openrouter_models.json`）
  - Fireworks: `https://api.fireworks.ai/inference/v1/models`（Bearer: `$FIREWORKS_SCRIPT`）
- thinking必須の実装（主に“執筆/審査”タスク）:
  - `configs/llm_task_overrides.yaml` で `options.extra_body.reasoning.enabled=true` を付与（章執筆/品質ゲート等）
  - `script_semantic_alignment_check` は **出力が厳格JSON** のため、`response_format: json_object` を使って「思考ありでもJSONが壊れにくい」側に寄せる（DeepSeek固定 / Kimiへフォールバックしない）。

### 2.2 画像生成（サムネ / 動画内画像）

正本（重要）:
- `configs/image_models.yaml`（ImageClient: task→tier→model）
- `configs/image_model_slots.yaml`（運用コード: `g-1` / `f-1` / `f-3` / `f-4` など）
- サムネ: `workspaces/thumbnails/templates.json: channels[].templates[].image_model_key`
- 動画内画像（SRT→画像）: `packages/video_pipeline/config/channel_presets.json: channels.<CH>.image_generation.model_key`
  - 実行時は `packages/video_pipeline/src/srt2images/orchestration/pipeline.py` が `IMAGE_CLIENT_FORCE_MODEL_KEY_VISUAL_IMAGE_GEN` を自動セットする。

運用ルール（重要）:
- 無断で tier 候補を増やしたり順序変更しない（ImageClientのround-robinで“既定”がズレるため）。
- 「一貫性」: 画像プロンプトのガードレール +（Kontextは）参照画像（input_image）で人物/場面のブレを抑える。
- 一時切替（ファイル編集なし / その実行だけ）:
  - 動画内画像: `IMAGE_CLIENT_FORCE_MODEL_KEY_VISUAL_IMAGE_GEN=g-1`（Gemini）/ `f-1`（Flux schnell）/ `f-4`（Flux max）
  - サムネ: `IMAGE_CLIENT_FORCE_MODEL_KEY_THUMBNAIL_IMAGE_GEN=g-1`（Gemini）/ `f-4`（Flux max）

---

## 3. タスク→用途（運用での意味）

### 3.1 Script pipeline（台本）
- `script_topic_research`: リサーチ/材料集め（重い推論）
- `script_outline`: 構成（重い推論、thinking高）
- `script_master_plan_opus`（任意）: 設計図（master plan）のサマリを Opus 4.5 で **1回だけ**補強（デフォルトOFF・allowlist必須）
- `script_chapter_brief`: 章の狙い（重い推論、thinking高）
- `script_chapter_draft`: 章ドラフト（重い推論、thinking高）
- `script_chapter_review`（保留）: 章レビュー（`script_enhancement` 廃止のため通常は呼ばれない）
- `script_cta`（既定OFF）: CTA/締め（任意。コスト削減のため既定OFF）
- `script_quality_check`（廃止）: 全体品質チェック（`script_validation` に統合。通常は使わない）
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
- `visual_image_gen`: 画像生成（ImageClient。正本: `configs/image_models.yaml` + slot: `configs/image_model_slots.yaml` / CH別固定: `packages/video_pipeline/config/channel_presets.json`）

### 3.4 Belt/Title（補助）
- `belt_generation`: ベルト文言生成（json_object）
- `title_generation`: タイトル生成

---

## 4. 実行時の重要な補足（壊さないための知識）

- `video_pipeline` は実行時に `IMAGE_CLIENT_FORCE_MODEL_KEY_VISUAL_IMAGE_GEN` をセットして画像モデルを固定する（チャンネルpreset由来）。
  - 値は `configs/image_models.yaml` の model_key か、`configs/image_model_slots.yaml` の slot code（例: `f-4`）を使う。
- “remotionは未実装/未運用”のため、現行は CapCut 主線に合わせてタスク/モデルを調整する。

### 4.0 モデル仕様の鮮度確認（OpenRouter）

目的: 「reasoning 等の対応パラメータ」「max_completion_tokens」などは鮮度が命なので、**憶測で決めない**。

- API（最新）: `https://openrouter.ai/api/v1/models`
- ローカル更新（キャッシュ更新）:
  - `./scripts/with_ytm_env.sh python3 packages/script_pipeline/tools/openrouter_models.py`
  - 出力: `packages/script_pipeline/config/openrouter_models.json`

### 4.1 CLI/Env での一時上書き（ルーター改造なしで可変にする）

目的: 実験/比較/コスト最適化のため、**設定ファイルを編集せず**に「この実行だけ」モデルを差し替える。

- **推奨: 数字スロットで固定（モデル名を書かない）**
  - `LLM_MODEL_SLOT=0`（default）: `configs/llm_model_slots.yaml: slots.0`（現行: non-script=OpenRouter主線 / script=Fireworks主線）
  - `LLM_MODEL_SLOT=1/2/...`: `configs/llm_model_slots.yaml: slots.<N>` を選ぶ
  - 個別調整は `configs/llm_model_slots.local.yaml`（git管理しない）で上書きする
  - 使い方（例）:
    - `./scripts/with_ytm_env.sh --llm-slot 2 python3 ...`
    - `./scripts/with_ytm_env.sh 2 python3 ...`（先頭が整数ならスロットとして解釈）
    - 互換: `LLM_FORCE_MODELS=2`（legacy）が入っていても **スロット2** として扱われる（推奨は `LLM_MODEL_SLOT`）
  - 重要:
    - スロットは strict 扱い（既定: 先頭モデルのみ）。複数モデルを試すのは `allow_fallback=true` を明示した時だけ
    - 非`script_*` は API 失敗時に THINK へ（モデル/プロバイダの自動すり替えはしない）
    - `script_*` は THINK へ行かない（API停止時は即停止・記録）。OpenRouter で回すなら `YTM_SCRIPT_ALLOW_OPENROUTER=1` または slot 側 `script_allow_openrouter: true`
    - 注: 本repoでは “モデル指定は model code（`fw-d-1` 等）” が正。`or_`/`fw_` などの内部キーは運用では使わない。

- **実行モード（どこで動くか）も slot で固定（運用のブレ防止）**
  - `LLM_EXEC_SLOT=0`（default）: 通常（api / codex_exec.yaml に従う / failover既定ON）
  - `LLM_EXEC_SLOT=1`: codex exec 強制ON（許可taskのみ）
  - `LLM_EXEC_SLOT=2`: codex exec 強制OFF
  - `LLM_EXEC_SLOT=3`: THINK MODE（pending を作る）
  - `LLM_EXEC_SLOT=4`: AGENT MODE（pending を作る）
  - `LLM_EXEC_SLOT=5`: API→THINK failover をOFF（非scriptのみ。script_* は停止）
  - 使い方（例）:
    - `./scripts/with_ytm_env.sh --exec-slot 3 python3 ...`
    - `python -m script_pipeline.cli run-all --channel CH06 --video 033 --exec-slot 3`

- 互換/緊急デバッグ（通常運用では使わない。`YTM_ROUTING_LOCKDOWN=1` では停止）:
  - 全タスク共通（model chain を固定）:
    - `LLM_FORCE_MODELS="fw-d-1,open-k-1"`（カンマ区切り。**モデルコード**は `configs/llm_model_codes.yaml`）
      - 互換: `deepseek/deepseek-v3.2-exp`（model id）や `gpt-5-mini`（Azure deployment）も **一意に解決できる場合のみ** model key に自動解決される（推奨は常に model code 指定）。
      - 注: `script_*` task では OpenRouter モデルは既定でフィルタされる（Fireworks-only; Fireworksが落ちたら停止）。`script_*` で OpenRouter を許可する場合は `YTM_SCRIPT_ALLOW_OPENROUTER=1` または slot 側 `script_allow_openrouter: true`。
  - タスク別（task→model chain）:
    - `LLM_FORCE_TASK_MODELS_JSON='{"script_outline":["fw-d-1","open-k-1"],"tts_annotate":["fw-d-1"]}'`
  - 使う場合は `YTM_EMERGENCY_OVERRIDE=1` を同時にセットして「この実行だけ」例外扱いにする
- CLI対応（入口側が上記 env を自動セット）:
  - `python -m script_pipeline.cli run-all --channel CH06 --video 033 --llm-slot 0`
  - `python3 scripts/ops/script_runbook.py resume --channel CH06 --video 033 --llm-slot 4`
  - `PYTHONPATH=".:packages" python -m audio_tts.scripts.run_tts --channel CH06 --video 033 --input ... --llm-slot 2`
  - `python3 scripts/format_srt_linebreaks.py path/to/in.srt --in-place --llm-slot 2`

### 4.2 Azure/非Azure 50/50 ルーティング（運用レバー）

目的: コスト/品質比較のため、同一タスクを Azure とそれ以外で **約半々**に振り分ける。

- 有効化: `LLM_AZURE_SPLIT_RATIO=0.5`
- ルーティングキー: `LLM_ROUTING_KEY`
  - `script_pipeline` は 1エピソード単位で固定になるよう `LLM_ROUTING_KEY={CH}-{NNN}` を自動設定する（同一動画の全ステージが同じ系統になりやすい）。
- 無効化: `LLM_AZURE_SPLIT_RATIO` を未設定（または `0`）

※ 実体ルーター（現行）: `packages/factory_common/llm_router.py`（設定: `configs/llm_router.yaml`）

#### 4.1.1 重要: heavy_reasoning は「台本品質」枠（高コスパ優先）

- `heavy_reasoning` は **台本生成/構成/整合チェック**の主力。まず **OpenRouter の高品質・低コスト枠**（例: DeepSeek V3 系）を優先する。
- `heavy_reasoning` は Azure/OpenAI GPT（例: `gpt-5-mini`）を **禁止**（フォールバックでも使わない。ルーターが除外する）。

### 4.3 「どのLLMが書いたか」を確実に残す（証跡）

- 正本: `workspaces/scripts/{CH}/{NNN}/status.json`
  - `stages.*.details.llm_calls[]` に provider/model/request_id/chain などを記録する。
- 参照用マニフェスト: `workspaces/scripts/{CH}/{NNN}/script_manifest.json`
  - status の内容と、`artifacts/llm/*.json` を同梱して追跡できるようにする。

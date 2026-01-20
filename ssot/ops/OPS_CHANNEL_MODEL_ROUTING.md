# OPS_CHANNEL_MODEL_ROUTING.md

目的: **チャンネル単位で「どの処理がどのモデルを使うか」**を 1 枚に集約し、モデル名/YAML書き換え運用を撲滅する。

---

## 0) まず覚える3点（チャンネル差分）

チャンネルで違うのは **この3つだけ**:

- サムネ画像（`thumbnail_image_gen`）
- 台本LLM（`script_*`）
- 動画内画像（`visual_image_gen`）

UI（`/model-policy`）では、この3点セットを **1つのコード**で表す:

- 形式: `<thumb>_<script>_<video>`（suffix `@xN` は省略可）
  - `<thumb>`: サムネ画像の **画像コード**（例: `g-1`, `f-4`）
  - `<script>`: 台本の **LLMコード**（例: `script-main-1`）
  - `<video>`: 動画内画像の **画像コード**（例: `g-1`, `f-1`）
  - `@xN`（省略可）: 実行モードの共有用（例: `@x3` = THINK MODE）

例:

- CH01 を「サムネ=Gemini / 台本=共通 / 動画内画像=Flux Max」で回す（例）: `g-1_script-main-1_f-4`
- CH02 を「サムネ=Gemini / 台本=共通 / 動画内画像=Flux schnell」で回す（例）: `g-1_script-main-1_f-1`
- THINK MODE で回す（例）: `g-1_script-main-1_f-1@x3`
  - 意味: `LLM_EXEC_SLOT=3`（`configs/llm_exec_slots.yaml`）
  - 注: THINK はデフォルト運用（pending）。台本も含め、**対話型AIエージェントが仕上げて進める**（API失敗→THINK 自動切替は禁止）。

### 0.1 コード早見表（画像）

| code | 意味 | ざっくり用途 |
|---|---|---|
| `g-1` | Gemini（画像生成） | いま安定して通る前提 |
| `i-1` | Imagen 4 Fast（Gemini API） | 即時の比較/リテイク用（速い/安い） |
| `f-1` | FLUX schnell | 速い（動画内画像のデフォ候補） |
| `f-3` | FLUX pro | 高品質（動画内画像の候補） |
| `f-4` | FLUX max | 最高品質（サムネ/重要シーン向け） |

### 0.2 `@xN`（実行モード）早見表

| `@xN` | 意味 |
|---|---|
| `@x0` | 通常（LLM API） |
| `@x1` | Codex exec 強制ON（自動）。※「AIエージェントCodex（pending運用）」ではない |
| `@x3` | THINK（非台本タスクのみエージェントが代行 / pending。`script_*` は停止） |

### 0.3 台本（`script_*`）は基本 “共通” です

- チャンネルごとに台本モデルを切り替える運用は **基本しない**（ブレ防止）。
- 台本の固定は `configs/llm_task_overrides.yaml` / 数字スロットで統制する（モデル名/YAML書き換え運用をしない）。

### 0.4 ルーティング優先順位（固定・迷わない）

通常運用（`YTM_ROUTING_LOCKDOWN=1`）の優先順位は **この順**:

1. `configs/llm_task_overrides.yaml` の `tasks.<task>.models`（pin: 台本/一部タスク）
2. `LLM_MODEL_SLOT`（`configs/llm_model_slots.yaml` の tier→model code）
3. `configs/llm_router.yaml` の `tiers`（最後のデフォルト）

注（固定）:
- **LLM API 間の自動フォールバックはしない**（別プロバイダ/別モデルへ勝手にすり替えない）。
- 通常運用は “先頭1つ固定” を前提にし、切替は **数字スロット**で明示する。

例外（debug/incident のみ）:
- `LLM_FORCE_MODELS` / `LLM_FORCE_TASK_MODELS_JSON` などの “直接上書き” は通常運用では禁止（`YTM_EMERGENCY_OVERRIDE=1` の時だけ）。

注:

- 画像は `IMAGE_CLIENT_FORCE_MODEL_KEY_*` による **実行時 override** があるため、UIでは `effective` と `config` を併記する。
- 台本（`script_*`）は **現状チャンネルごとの切替は無い**（task override / 数字スロットで統制）。追加する場合はSSOTで設計を追加する。

---

## 1) 触っていい“操作レバー”だけ（ここ以外を触らない）

### テキストLLM（台本以外の一般タスク）
- **数字スロット**: `LLM_MODEL_SLOT`（= `configs/llm_model_slots.yaml`）
- 入口固定: CLI の `--llm-slot <N>`（モデル名を直書きしない）

### 実行モード（どこで動く？）
- **数字スロット**: `LLM_EXEC_SLOT`（= `configs/llm_exec_slots.yaml`）
  - 例: `--exec-slot 3`（THINK MODE）, `--exec-slot 1`（codex exec 強制ON）
- 入口固定: `./scripts/with_ytm_env.sh --exec-slot <N> ...`（env直書きの増殖を防ぐ）

### 台本（script_*）
- **台本は task override で固定**（= `configs/llm_task_overrides.yaml`）
- UI/デバッグ上書きは `configs/llm_task_overrides.local.yaml`（git管理しない）にのみ書く（SSOTの破壊防止）
- `script_*` は **失敗時に停止・記録**（自動フォールバックで続行しない）

### 画像（動画用 / サムネ）
- **画像コード**: `configs/image_model_slots.yaml`
- 動画用画像（SRT→images）: `packages/video_pipeline/config/channel_presets.json` の `image_generation.model_key`
- サムネ: `workspaces/thumbnails/templates.json` の `image_model_key`
- UI:
  - 方針ビュー（表）: `/model-policy`
  - 設定編集: `/image-model-routing`

---

## 2) テキストLLM: スロット（概要）

`configs/llm_model_slots.yaml`（slot は **“ルーティングの型”**）

- `slot 0` `openrouter_main`（デフォルト）
  - `heavy_reasoning=txt-main-hr-1`
  - `standard=txt-main-std-1`
  - `cheap=txt-main-cheap-1`
  - `vision_caption=txt-vision-caption-1`
  - `web_search=txt-web-search-1`
  - `master_plan_opus=txt-master-plan-opus-1`
  - `script_*` は `script-main-1`（Fireworks固定 / 自動フォールバックしない）
- `slot 1` `openrouter_kimi_all`（全 tier を Kimi 固定）
- `slot 2` `openrouter_mistral_all`（全 tier を Mistral free 固定）
- `slot 3` `fireworks_deepseek_v3_2`（全 tier を Fireworks DeepSeek 固定）
- `slot 4` `fireworks_glm_4p7`
- `slot 5` `fireworks_mixtral_8x22b`

---

## 3) 台本（script_*）: 固定チェーン（概要）

`configs/llm_task_overrides.yaml`

- main: `script-main-1`
- 固定: 自動フォールバックしない（`models` は常に1つ）
- 参考（比較用・自動では使わない）:
  - `fw-glm-4p7-1`, `fw-kimi-thinking-1`, `fw-mixtral-8x22b-1`
- 非台本でも pin されるタスク（例）:
  - `visual_image_cues_plan=visual-cues-plan-main-1`

（コード→実体の対応は `configs/llm_model_codes.yaml`）

---

## 4) 画像コード（正本）

`configs/image_model_slots.yaml`

- `img-gemini-flash-1`（alias: `g-1`）
- `img-imagen-4-fast-1`（alias: `i-1`）
- `img-flux-schnell-1`（alias: `f-1`）
- `img-flux-pro-1`（alias: `f-3`）
- `img-flux-max-1`（alias: `f-4`）

既定（SSOT）:
- tier default は `configs/image_models.yaml` の `tiers` に従う（現行は `gemini_2_5_flash_image` が既定）。
- ただし通常運用では **tier default に頼らず**、次の SoT で `model_key` を明示してブレを潰す:
  - 動画内画像: `packages/video_pipeline/config/channel_presets.json`
  - サムネ: `workspaces/thumbnails/templates.json`

---

## 5) 画像ポリシー（チャンネル別の要件）: まずここを見る

### 5.1 動画用画像（SRT→images / visual_image_gen）

SoT（正本）:
- CH別の固定: `packages/video_pipeline/config/channel_presets.json` の `channels.<CH>.image_generation.model_key`
- 画面: `/model-policy`（effective確認） / `/image-model-routing`（編集）

禁止（通常運用）:
- `.env` に `IMAGE_CLIENT_FORCE_MODEL_KEY_VISUAL_IMAGE_GEN` / `IMAGE_CLIENT_FORCE_MODEL_KEY` を恒久セットしない（ロックダウンで停止）。

許可（incident/debug のみ・その実行だけ）:
- `IMAGE_CLIENT_FORCE_MODEL_KEY_VISUAL_IMAGE_GEN=f-1 ./ops video ...`

安全（強制）:
- **禁止（動画内画像）**: `visual_image_gen`（動画内画像）では Gemini 3 系の画像モデルは使わない（例: `gemini_3_pro_image_preview`, `openrouter_gemini_3_pro_image_preview`）。
  - `IMAGE_CLIENT_FORCE_MODEL_KEY_VISUAL_IMAGE_GEN` / `IMAGE_CLIENT_FORCE_MODEL_KEY_IMAGE_GENERATION` / `IMAGE_CLIENT_FORCE_MODEL_KEY` に `gemini-3` / `gemini_3` を含む値を入れた時点で停止する（ガードあり）。

### 5.2 サムネ（thumbnail_image_gen）

SoT（正本）:
- `workspaces/thumbnails/templates.json` の `templates[].image_model_key`
- 画面: `/thumbnails`（運用） / `/image-model-routing`（編集） / `/model-policy`（effective確認）

禁止（通常運用）:
- `.env` に `IMAGE_CLIENT_FORCE_MODEL_KEY_THUMBNAIL_IMAGE_GEN` / `IMAGE_CLIENT_FORCE_MODEL_KEY` を恒久セットしない（ロックダウンで停止）。

固定（運用）:
- サムネ背景生成（`thumbnail_image_gen`）は **Gemini 2.5 Flash Image（g-1）固定**。
- `IMAGE_CLIENT_FORCE_MODEL_KEY_THUMBNAIL_IMAGE_GEN` による上書きは運用では使わない（ロックダウンで停止 / debugでも使わない）。

---

## 6) その他のLLM処理（共通）: task → tier → slot

「Bテキスト」「画像プロンプト整形」「TTS補助」などの細かい処理は **全チャンネル共通**。

- task（例）:
  - `belt_generation`（Bテキスト）
  - `visual_prompt_refine`（画像プロンプト整形）
  - `visual_thumbnail_caption`（サムネ要約）
- TTS（読み/分割/ポーズ等）: **推論=対話型AIエージェント / 読みLLM無効**（VOICEVOX: prepass mismatch=0 / VOICEPEAK: prepass→合成→サンプル再生OK）。`SKIP_TTS_READING=1` が既定/必須（`YTM_ROUTING_LOCKDOWN=1` 下で `SKIP_TTS_READING=0` は禁止）。
  - 正本入口: `./ops audio -- --channel CHxx --video NNN`
- これらは `configs/llm_router.yaml` の `tasks.<task>.tier` で tier が決まり、
  **tier は `LLM_MODEL_SLOT`（`configs/llm_model_slots.yaml`）でモデルコードに解決**される。
- 一覧はUI `/model-policy` の「その他のLLM処理（共通）」表で確認する。

---

## 7) 「いまの設定」を見る場所（手で表を保守しない）

チャンネル別の「現状」は更新頻度が高く、SSOTに **手書きのスナップショット表**を置くとズレます。

- 一覧（入口固定）: UI `/model-policy`（effective + config + ENV override）
- 編集: UI `/image-model-routing`（画像） / SSOT（台本LLM・スロット）
- 生成チェック: `python3 scripts/ops/build_ssot_catalog.py --check`

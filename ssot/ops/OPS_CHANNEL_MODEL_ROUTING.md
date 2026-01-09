# OPS_CHANNEL_MODEL_ROUTING.md

目的: **チャンネル単位で「どの処理がどのモデルを使うか」**を 1 枚に集約し、モデル名/YAML書き換え運用を撲滅する。

---

## Architecture（固定の解決順 / 迷子ゼロ）

モデル決定は「どこを見れば何が変わるか」を固定し、**人間/AIが迷う余地を消す**。

### テキストLLM（LLMRouter）
- 入力: `task` 名（コードはモデル名を書かない）
- 解決順（固定 / lockdown既定ON）:
  1) **tier決定**:
     - `configs/llm_task_overrides.yaml:tasks.<task>.tier`（任意）
     - `configs/llm_router.yaml:tasks.<task>.tier`（ベース）
     - 未定義は `standard`
  2) **model selector chain決定（最重要）**:
     - `configs/llm_model_slots.yaml`（`LLM_MODEL_SLOT` / 既定は `default_slot=0`）
       - `script_*` は `script_tiers` がある時は **必ず `script_tiers`** を使う（`tiers` へはフォールバックしない）
       - `script_allow_openrouter=true` の slot だけが OpenRouter を許可（それ以外は script_* で OpenRouter を弾いて停止する）
  3) slot が該当 tier を持たない場合のフォールバック（通常は出ない）:
     - `configs/llm_task_overrides.yaml:tasks.<task>.models`（任意）
     - `configs/llm_router.yaml:tiers.<tier>`（ベース）
  4) selector を実体へ解決:
     - `configs/llm_model_codes.yaml`（code → `llm_router.yaml:models.<model_key>`）
     - `configs/llm_router.yaml:models.<model_key>`（provider / model_name or deployment / capabilities）
- 実行モード（固定）:
  - `LLM_EXEC_SLOT`（`configs/llm_exec_slots.yaml`：api/think/agent/codex exec/failover）

### 画像（ImageClient）
- 入力: image task（例: `thumbnail_image_gen`, `visual_image_gen`）
- 解決順（固定）:
  1) `configs/image_task_overrides.yaml`（profile: `IMAGE_CLIENT_PROFILE`）
  2) `configs/image_models.yaml`（task→tier→models）
  3) `configs/image_model_slots.yaml`（運用で固定したい場合のコード）
  4) 入口SoTでの明示（ブレ防止）:
     - 動画内画像: `packages/video_pipeline/config/channel_presets.json`
     - サムネ: `workspaces/thumbnails/templates.json`

### UI（SSOT = read-only）
- UI は `GET /api/ssot/catalog` を参照し、**config と effective（env/slot/profile込み）**を同じ画面で見られるようにする。

## 0) まず覚える3点（チャンネル差分）

チャンネルで基本的に違うのは **この3つだけ**:

- サムネ画像（`thumbnail_image_gen`）
- 台本LLM（`script_*`）
- 動画内画像（`visual_image_gen`）

UI（`/model-policy`）では、この3点セットを **1つのコード**で表す:

- 形式: `<thumb>_<script>_<video>`（必要なら suffix `@xN` を付ける）
  - `<thumb>`: サムネ画像の **画像コード**（例: `g-1`, `f-4`）
  - `<script>`: 台本の **LLMコード**（slot0 既定: `script-main-1`）
  - `<video>`: 動画内画像の **画像コード**（例: `g-1`, `f-1`）
  - `@xN`（任意）: 実行モードの共有用（例: `@x3` = THINK MODE）

例:

- CH01 を「サムネ=Gemini / 台本=共通 / 動画内画像=Flux Max」で回す（例）: `g-1_script-main-1_f-4`
- CH02 を「サムネ=Gemini / 台本=共通 / 動画内画像=Flux schnell」で回す（例）: `g-1_script-main-1_f-1`
- THINK MODE で回す（例）: `g-1_script-main-1_f-1@x3`
  - 意味: `LLM_EXEC_SLOT=3`（`configs/llm_exec_slots.yaml`）

### 0.1 コード早見表（画像）

| code | 意味 | ざっくり用途 |
|---|---|---|
| `g-1` | Gemini（画像生成） | いま安定して通る前提 |
| `f-1` | FLUX schnell | 速い（動画内画像のデフォ候補） |
| `f-3` | FLUX pro | 高品質（動画内画像の候補） |
| `f-4` | FLUX max | 最高品質（サムネ/重要シーン向け） |

### 0.2 `@xN`（実行モード）早見表

| `@xN` | 意味 |
|---|---|
| `@x0` | 通常（LLM API） |
| `@x3` | THINK（エージェントが代行 / pending） |

### 0.3 台本（`script_*`）は基本 “共通” です

- チャンネルごとに台本モデルを切り替える運用は **基本しない**（ブレ防止）。
- 台本の固定は **数字スロット（`configs/llm_model_slots.yaml` の `script_tiers`）**で統制する（モデル名/YAML書き換え運用をしない）。

注:

- 画像は `IMAGE_CLIENT_FORCE_MODEL_KEY_*` による **実行時 override** があるため、UIでは `effective` と `config` を併記する。
- 台本（`script_*`）は **現状チャンネルごとの切替は無い**（slotで統制）。必要ならSSOTで設計を追加する。

---

## 1) 触っていい“操作レバー”だけ（ここ以外を触らない）

### テキストLLM（台本以外の一般タスク）
- **数字スロット**: `LLM_MODEL_SLOT`（= `configs/llm_model_slots.yaml`）
- 推奨: CLI の `--llm-slot <N>`（モデル名を直書きしない）

### 実行モード（どこで動く？）
- **数字スロット**: `LLM_EXEC_SLOT`（= `configs/llm_exec_slots.yaml`）
  - 例: `--exec-slot 3`（THINK MODE）, `--exec-slot 1`（codex exec 強制ON）
- 推奨: `./scripts/with_ytm_env.sh --exec-slot <N> ...`（env直書きの増殖を防ぐ）

### 台本（script_*）
- **台本モデルは slot の `script_tiers` が正本**（= `configs/llm_model_slots.yaml`）
- task override（`configs/llm_task_overrides.yaml`）は tier/options/allow_fallback の統制に使う（モデル pin は原則ここでやらない）
- デバッグ用のローカル上書きは `.local.yaml` にのみ書く（git管理しない）:
  - slot: `configs/llm_model_slots.local.yaml`
  - task override: `configs/llm_task_overrides.local.yaml`
- `script_*` は **失敗時に停止・記録**（THINK へフォールバックしない）

### 禁止（通常運用）: legacy LLM config（`llm.yml` / `llm_client` / `llm_config`）
- LLM routing の正本は **router + codes/slots**（`configs/llm_router.yaml` + `configs/llm_task_overrides.yaml` + `LLM_MODEL_SLOT` / `LLM_EXEC_SLOT`）。
- `configs/llm.yml` / `factory_common.llm_client` / `factory_common.llm_config` は **互換/テスト用の legacy**。通常運用では使わない（迷子/矛盾を作るため）。
- 2026-01-09: ロックダウン（`YTM_ROUTING_LOCKDOWN=1` / default ON）では legacy 経由の実行を **停止**する。
  - 解除（debug only）: `YTM_ROUTING_LOCKDOWN=0` または `YTM_EMERGENCY_OVERRIDE=1`

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
  - `script_*` は `script-main-1`（Fireworks / DeepSeek v3.2 exp + thinking。失敗時は停止）
- `slot 1` `openrouter_kimi_all`（全 tier を Kimi 固定）
- `slot 2` `openrouter_mistral_all`（全 tier を Mistral free 固定）
- `slot 3` `fireworks_deepseek_v3_2`（全 tier を Fireworks DeepSeek 固定）
- `slot 4` `fireworks_glm_4p7`
- `slot 5` `fireworks_mixtral_8x22b`

---

## 3) 台本（script_*）: 正本は slot の `script_tiers`

正本（モデル選択）:
- `configs/llm_model_slots.yaml` の `slots.<id>.script_tiers.<tier>`（`LLM_MODEL_SLOT` / 既定は slot 0）
  - slot 0 既定（現状）:
    - `heavy_reasoning/standard/cheap → script-main-1`（DeepSeek v3.2 exp）
    - `master_plan_opus → txt-master-plan-opus-1`（Opus 4.5 / optional）
  - `script_allow_openrouter=true` の slot だけが OpenRouter を許可（false の slot で open-* を指定してもフィルタされ、結果は空→停止）

補助（品質/出力仕様）:
- `configs/llm_task_overrides.yaml` は tier/options/allow_fallback/system_prompt を持つ（モデル pin は原則ここでやらない）

（コード→実体の対応は `configs/llm_model_codes.yaml`）

---

## 4) 画像コード（推奨）

`configs/image_model_slots.yaml`

- `img-gemini-flash-1`（alias: `g-1`）
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

許可（incident/debug のみ・その実行だけ）:
- `IMAGE_CLIENT_FORCE_MODEL_KEY_THUMBNAIL_IMAGE_GEN=f-4 ./ops thumbnails build ...`

備考:
- **許可（サムネ）**: サムネ背景生成（`thumbnail_image_gen`）に限り Gemini 3 系の画像モデルは **使っても良い**（必要時のみ明示して使う）。
  - 例: `IMAGE_CLIENT_FORCE_MODEL_KEY_THUMBNAIL_IMAGE_GEN=gemini_3_pro_image_preview ./ops thumbnails build ...`

---

## 6) その他のLLM処理（共通）: task → tier → slot

「Bテキスト」「画像プロンプト整形」「TTS補助」などの細かい処理は、基本的に **全チャンネル共通**。

- task（例）:
  - `belt_generation`（Bテキスト）
  - `visual_prompt_refine`（画像プロンプト整形）
  - `visual_thumbnail_caption`（サムネ要約）
  - `tts_segment`（TTS分割）
- これらは `configs/llm_router.yaml` の `tasks.<task>.tier` で tier が決まり、
  **tier は `LLM_MODEL_SLOT`（`configs/llm_model_slots.yaml`）でモデルコードに解決**される。
- 一覧はUI `/model-policy` の「その他のLLM処理（共通）」表で確認する。

---

## 7) 「いまの設定」を見る場所（手で表を保守しない）

チャンネル別の「現状」は更新頻度が高く、SSOTに **手書きのスナップショット表**を置くとズレます。

- 一覧（推奨）: UI `/model-policy`（effective + config + ENV override）
- CLI（read-only / 時点スナップショット）:
  - `./ops snapshot model-policy`（CH×サムネ/台本/動画内画像の「3点コード」+ 実モデルを1表で出す）
  - `./ops snapshot model-policy --write-report`（`workspaces/logs/regression/model_policy_snapshot/` に timestamp 付きで保存）
    - `video_src=tier_default` は「channel preset 未設定なので tier default（通常 g-1）」の意味
- 編集: UI `/image-model-routing`（画像） / SSOT（台本LLM・スロット）
- 生成チェック: `python3 scripts/ops/build_ssot_catalog.py --check`

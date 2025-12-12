# OPS_IO_SCHEMAS — フェーズ別I/Oスキーマ（実データ観測ベース）

目的:
- リファクタリング/ゴミ判定/自動cleanup/ログ整理の前提となる **I/O（正本/中間/生成物）** を、ファイル単位で確定する。
- 「何がどこにどんな形式で出るか」を固定し、パス移設やモジュール分割でも壊れない設計にする。

正本フロー: `ssot/OPS_CONFIRMED_PIPELINE_FLOW.md`  
ログ: `ssot/OPS_LOGGING_MAP.md`  
生成物ライフサイクル: `ssot/PLAN_OPS_ARTIFACT_LIFECYCLE.md`

---

## 0. 共通ルール

- **SoT（正本）**: その工程の真実。下流は原則 SoT のみを参照する。
- **Intermediate（中間）**: 再生成可能。保持/削除ルールは `PLAN_OPS_ARTIFACT_LIFECYCLE`。
- **Schemaは“許容”で定義**: 実データには揺れがあるため、必須キー/任意キーを明示する。

---

## 1. Planning（企画/進捗）— CSV

### 1.1 SoT
- `progress/channels/CHxx.csv`

### 1.2 ヘッダ（例: CH01 の観測）
- `No.`
- `チャンネル`
- `動画番号`
- `動画ID`
- `YouTubeID`
- `タイトル`
- `台本`
- `台本番号`
- `作成フラグ`
- `進捗`
- `品質チェック結果`
- `文字数`
- `サムネタイトル`
- `AI向け画像生成プロンプト (背景用)`
- `テキスト配置・デザイン指示 (人間/ツール向け)`
- `サムネ用DALL-Eプロンプト（URL・テキスト指示込み）`
- `企画意図`
- `ターゲット層`
- `具体的な内容（話の構成案）`
- `更新日時`
- `音声整形`
- `音声検証`
- `音声生成`
- `音声品質`
- `DALL-Eプロンプト（URL・テキスト指示込み）`
- `台本パス`

### 1.3 I/O上の注意
- `動画番号` は `NNN`（3桁ゼロ埋め）として他SoTと突合する。
- `台本パス` 等、旧パスが混入しやすい列は **paths SSOT導入後に正規化**する（`ssot/PLAN_STAGE1_PATH_SSOT_MIGRATION.md`）。

---

## 2. Script（台本）— video_dir と status.json

### 2.1 SoT（動画単位）
- `script_pipeline/data/{CH}/{NNN}/status.json`

### 2.2 ディレクトリI/O（観測）
- `script_pipeline/data/{CH}/{NNN}/content/assembled.md`（最終台本入力の基本）
- `script_pipeline/data/{CH}/{NNN}/logs/*_prompt.txt`, `*_response.json`（LLM実行時の証跡。存在しない動画もある）
- `script_pipeline/data/{CH}/{NNN}/audio_prep/`（TTS中間。gitignore推奨）

### 2.3 status.json（観測スキーマ）
必須（期待）:
- `script_id`: `CHxx-NNN`
- `channel`: `CHxx`
- `metadata`: dict（少なくとも `title` を含む想定）
- `status`: string
- `stages`: dict（stage_name → stage_state）

観測される追加キー（Legacy/任意）:
- `channel_code`, `video_number`
- `created_at`, `updated_at`

stage_state（観測）:
- `status`: `"pending" | "processing" | "completed" | "failed"`
- `details`: dict（存在する場合）

---

## 3. Audio/TTS（音声・字幕）— final dir と log.json

### 3.1 下流参照SoT（final）
- `audio_tts_v2/artifacts/final/{CH}/{NNN}/`
  - `{CH}-{NNN}.wav`
  - `{CH}-{NNN}.srt`
  - `log.json`
  - `a_text.txt`

### 3.2 log.json（観測スキーマ）
トップレベル（必須）:
- `channel`: `CHxx`
- `video`: `NNN`
- `engine`: string（例: voicevox/voicepeak 等）
- `timestamp`: number（epoch）
- `segments`: list

segments[*]（観測キー例）:
- `text`: string
- `duration`: number
- `section_id`: string/number
- `heading`: string
- `reading`: dict/list（読み解決の結果）
- `tokens`: list（トークン/ブロック）
- `verdict`: dict（監査/危険語判定）
- `voicevox`: dict（音声合成メタ）
- `mecab`: dict（形態素）
- `pre` / `post`: dict（前処理/後処理メタ）

---

## 4. Video（SRT→画像→CapCut）— run_dir と JSON

### 4.1 SoT（run単位）
- `commentary_02_srt2images_timeline/output/{run_id}/`

観測される代表ファイル:
- `image_cues.json`
- `images/*.png`
- `capcut_draft`（CapCut projects への symlink）
- `capcut_draft_info.json`
- `auto_run_info.json`
- `belt_config.json`（run により存在しない場合あり）
- `channel_preset.json`, `persona.txt`, `{CH}-{NNN}.srt`（run により存在）

### 4.2 image_cues.json（観測スキーマ）
トップレベル（観測）:
- `fps`: number
- `imgdur`: number
- `crossfade`: number
- `size`: array/obj（解像度情報）
- `cues`: list

cues[*]（観測キー例）:
- `index`: number
- `text`: string（字幕/音声断片）
- `summary`: string
- `prompt`: string
- `start_sec`, `end_sec`: number
- `start_frame`, `end_frame`: number
- `duration_sec`, `duration_frames`: number
- `role_tag`, `section_type`, `visual_focus`, `emotional_tone`
- `context_reason`, `use_persona`
- `input_images`: list（参照画像のヒント）

### 4.3 belt_config.json（観測スキーマ）
トップレベル（観測）:
- `main_title`: string
- `episode`: string
- `total_duration`: number
- `opening_offset`: number
- `belts`: list

belts[*]（観測）:
- `text`: string
- `start`: number
- `end`: number

### 4.4 auto_run_info.json（観測スキーマ）
用途: 実行パラメータ/再現性（run の“証跡”）
- `timestamp`: string（ISO）
- `channel`: string
- `run_dir`: string
- `srt`: string
- `template`: string
- `belt_mode`: string
- `opening_offset`: number
- `duration_sec`: number
- `timeout_ms`: number
- `resume`: bool
- `force`: bool
- `fallback_if_missing_cues`: bool
- `draft`/`draft_name`: string
- `images`: dict/list（生成/配置のメタ）
- `nanobanana`: dict（画像生成側のメタ）
- `timings`: dict（工程時間）

### 4.5 capcut_draft_info.json（観測スキーマ）
用途: CapCut 側ドラフト生成の証跡
- `created_at`: string（ISO）
- `draft_name`: string
- `draft_path`: string（CapCut projects path）
- `project_id`: string
- `template_used`: string
- `srt_file`: string
- `title`: string
- `transform`: dict
- `crossfade_sec`, `fade_duration_sec`: number

---

## 5. Thumbnails（サムネ）— projects.json

### 5.1 SoT
- `thumbnails/projects.json`

### 5.2 projects.json（観測の形）
トップレベル:
- dict（`projects`: list）

projects[*]（観測キー例）:
- `channel`: `CHxx`
- `video`: `NNN`
- `status`: string
- `selected_variant_id`: string/None
- `variants`: list

variants[*]（観測キー例）:
- `id`: string
- `image_path`: string（例: `CH01/002/002_calm.png`）
- `image_url`: string（外部URLを使う場合）

### 5.3 物理配置（重要）
- UI/Backend は `/thumbnails/assets/{image_path}` を配信する設計のため、物理ファイルは `thumbnails/assets/{image_path}` に寄せる。
- 旧来の `thumbnails/CHxx_<チャンネル名>/...` は Legacy 資産として移行/アーカイブ対象。

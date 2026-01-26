# OPS_IO_SCHEMAS — フェーズ別I/Oスキーマ（実データ観測ベース）

目的:
- リファクタリング/ゴミ判定/自動cleanup/ログ整理の前提となる **I/O（正本/中間/生成物）** を、ファイル単位で確定する。
- 「何がどこにどんな形式で出るか」を固定し、パス移設やモジュール分割でも壊れない設計にする。

正本フロー: `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`  
ログ: `ssot/ops/OPS_LOGGING_MAP.md`  
生成物ライフサイクル: `ssot/plans/PLAN_OPS_ARTIFACT_LIFECYCLE.md`

---

## 0. 共通ルール

- **SoT（正本）**: その工程の真実。下流は SoT のみを参照する（例外を作るなら SoT を増やす）。
- **Intermediate（中間）**: 再生成可能。保持/削除ルールは `PLAN_OPS_ARTIFACT_LIFECYCLE`。
- **Schemaは“許容”で定義**: 実データには揺れがあるため、必須キー/省略可キーを明示する。
- **参照パス規約（PathRef）**: 共有される JSON/manifest/log には “ホスト固有の絶対パス” を埋め込まない。規約は `ssot/ops/OPS_PATHREF_CONVENTION.md` を正とする。

---

## 1. Planning（企画/進捗）— CSV

### 1.1 SoT
- `workspaces/planning/channels/CHxx.csv`

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
- `台本` / `台本パス` は **repo 相対パス**（例: `workspaces/scripts/CH01/001/content/assembled.md`）を正とする。
  - 絶対パスが混入している場合は、repo ルート配下であることを確認した上で相対へ正規化する（`ssot/completed/PLAN_STAGE1_PATH_SSOT_MIGRATION.md`）。

---

## 2. Script（台本）— video_dir と status.json

### 2.1 SoT（動画単位）
- `workspaces/scripts/{CH}/{NNN}/status.json`（正本）

### 2.2 ディレクトリI/O（観測）
- `workspaces/scripts/{CH}/{NNN}/content/assembled.md`（最終台本入力の基本）
- `workspaces/scripts/{CH}/{NNN}/logs/*_prompt.txt`, `*_response.json`（LLM実行時の証跡。存在しない動画もある）
- `workspaces/scripts/{CH}/{NNN}/audio_prep/`（TTS中間。git追跡しない）

### 2.3 Research（topic_research）— 検索/リサーチ中間
用途:
- ネタ切れ防止、ファクトチェックの“足場”を固定する（Aテキスト本文には URL を入れない）。

観測されるファイル:
- `workspaces/scripts/{CH}/{NNN}/content/analysis/research/search_results.json`（Web検索結果）
  - `schema`: `"ytm.web_search_results.v1"`
  - `provider`: string（例: `brave`, `openrouter:perplexity/sonar`, `disabled`）
  - `query`: string
  - `retrieved_at`: string（UTC ISO, `Z`）
  - `hits`: list
    - `title`: string
    - `url`: string（http/https）
    - `snippet`: string|null（省略可）
    - `source`: string|null（省略可）
    - `age`: string|null（省略可）
- `workspaces/scripts/{CH}/{NNN}/content/analysis/research/wikipedia_summary.json`（Wikipedia 抜粋）
  - `schema`: `"ytm.wikipedia_summary.v1"`
  - `provider`: string（例: `wikipedia`, `disabled`）
  - `query`: string（検索クエリ/推定タイトル）
  - `lang`: string（例: `ja`, `en`）
  - `retrieved_at`: string（UTC ISO, `Z`）
  - `page_title`: string|null
  - `page_id`: number|null
  - `page_url`: string|null（http/https）
  - `extract`: string|null（導入部の plaintext。空の場合あり）
- `workspaces/scripts/{CH}/{NNN}/content/analysis/research/research_brief.md`（LLMが参照する要約/論点）
- `workspaces/scripts/{CH}/{NNN}/content/analysis/research/references.json`（出典の機械参照用）
  - list of dict（観測キー例）:
    - `title`: string
    - `url`: string
    - `type`: string（例: `web`, `paper`）
    - `source`: string（省略可）
    - `year`: number|null（省略可）
    - `note`: string（省略可）
    - `confidence`: number（省略可）
- `workspaces/scripts/{CH}/{NNN}/content/analysis/research/fact_check_report.json`（完成台本ファクトチェック）
  - `schema`: `"ytm.fact_check_report.v1"`
  - `provider`: string（例: `codex`, `llm_router:...`, `disabled`）
  - `policy`: string（`disabled|auto|required`）
  - `verdict`: string（`pass|warn|fail|skipped`）
  - `generated_at`: string（UTC ISO, `Z`）
  - `input_fingerprint`: string（sha256; 同一入力の再実行抑止に使用）
  - `claims`: list
    - `id`: string（`c1` 等）
    - `claim`: string（検証対象の断言文）
    - `status`: string（`supported|unsupported|uncertain`）
    - `rationale`: string|null（省略可）
    - `citations`: list（省略可）
      - `source_id`: string（`s1` 等）
      - `url`: string
      - `quote`: string（抜粋内の“完全一致”のみ許可）

### 2.3 script_manifest.json（契約 / 仕組み化の核）
用途:
- Scriptフェーズの入力/出力/依存（status.json・assembled.md・LLM artifacts）を **1ファイルに固定**し、UI表示・移設・検証の基礎にする。

場所（期待）:
- `workspaces/scripts/{CH}/{NNN}/script_manifest.json`

トップレベル（期待）:
- `schema`: `"ytm.script_manifest.v1"`
- `generated_at`: string（UTC ISO）
- `repo_root`: string（repo absolute path）
- `episode`: dict（`id`, `channel`, `video`）
- `sot`: dict（`status_json` 等）
- `outputs`: dict（`assembled_md` 等）
- `notes`: string（省略可）

### 2.4 status.json（観測スキーマ）
必須（期待）:
- `script_id`: `CHxx-NNN`
- `channel`: `CHxx`
- `metadata`: dict（少なくとも `title` を含む想定）
- `status`: string
- `stages`: dict（stage_name → stage_state）

観測される追加キー（Legacy/省略可）:
- `channel_code`, `video_number`
- `created_at`, `updated_at`

`metadata`（観測: 追加フィールド）:
- `sheet_title`: string（Planning CSVのタイトルスナップショット）
- `alignment`: dict（Planning↔Scriptの整合スタンプ。UIの `整合` 列や下流ガードの根拠）
  - `schema`: `"ytm.alignment.v1"`
  - `computed_at`: string（UTC ISO, `Z`）
  - `planning_hash`: string（sha1）
  - `script_hash`: string（sha1）
  - `planning`: dict
    - `title`: string
    - `thumbnail_catch`: string（サムネプロンプト先頭行の『...』抽出。無い場合は空）
  - 例外系（疑義/未確定としてマーキングする場合）:
    - `suspect`: bool
    - `suspect_reason`: string

stage_state（観測）:
- `status`: `"pending" | "processing" | "completed" | "failed"`
- `details`: dict（存在する場合）

---

## 3. Audio/TTS（音声・字幕）— final dir と log.json

### 3.1 下流参照SoT（final）
- `workspaces/audio/final/{CH}/{NNN}/`（正本）
  - `{CH}-{NNN}.wav`
  - `{CH}-{NNN}.srt`
  - `log.json`
  - `a_text.txt`

### 3.2 audio_manifest.json（契約 / 仕組み化の核）
用途:
- Audioフェーズの **最終参照正本（final）** を1ファイルで要約し、下流（Video/UI/検証）が機械的に参照できるようにする。

場所（期待）:
- `workspaces/audio/final/{CH}/{NNN}/audio_manifest.json`

トップレベル（期待）:
- `schema`: `"ytm.audio_manifest.v1"`
- `generated_at`: string（UTC ISO）
- `repo_root`: string（repo absolute path）
- `episode`: dict（`id`, `channel`, `video`）
- `final_dir`: string（repo相対）
- `source`: dict（`a_text` 等）
- `artifacts`: dict（`wav`, `srt`, `log` 等）
- `notes`: string（省略可）

### 3.3 log.json（観測スキーマ）
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
- `workspaces/video/runs/{run_id}/`（正本）

観測される代表ファイル:
- `image_cues.json`
- `images/*.png`
- `capcut_draft`（CapCut projects への symlink）
  - 生成前は存在しない/壊れている（target無）ことがある。壊れたリンクの掃除は `scripts/ops/cleanup_broken_symlinks.py`。
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
用途: 実行パラメータ/再現性（run の“証跡”）。**途中停止/画像スキップ等の進捗も残す**（後段の progress 集計/UI のため）。

トップレベル（観測）:
- `schema`: string（例: `"ytm.auto_run_info.v2"`）（省略可; 後方互換）
- `timestamp`: string（ISO）
- `channel`: string（`CHxx`）
- `video` / `episode_id`: string（例: `"015"`, `"CH22-015"`）※解決できる場合
- `run_dir`: string（absolute）
- `srt_requested`: string（入力）
- `srt_effective`: string（SoT解決後の入力）
- `audio_wav_effective`: string（省略可）
- `title`: string（ドラフト/帯の最終タイトル）
- `title_source`: string（`cli|planning_csv|llm|fallback`）（省略可）
- `template`: string（CapCut template）
- `draft_root`: string（CapCut projects root）（省略可）
- `draft` / `draft_name`: string（draft 生成をスキップした場合は空/欠損可）
- `belt_mode`: string
- `opening_offset`: number
- `timeout_ms`: number
- `resume`: bool
- `force`: bool
- `fallback_if_missing_cues`: bool
- `nanobanana`: string（`batch|direct|none`）
- `images`: number（= cue_count）※従来互換
- `duration_sec`: number（= cues_end_sec）
- `timings`: dict（工程時間）
- `progress`: dict（工程別ステータス。省略可）
- `replacements`: list（差し替え履歴。省略可）
- `errors` / `warnings`: list（省略可）

progress（観測例）:
- `pipeline`: `{status, elapsed_sec?, error?}`
- `image_generation`: `{status, mode, expected, present, placeholders?}`
- `draft`: `{status, path?, error?}`
- `title_injection`: `{status, error?}`
- `timeline_manifest`: `{status, path?, validate?, error?}`
- `broll`: `{status, injected?, target?}`
- `belt`: `{status, mode, error?}`

### 4.5 capcut_draft_info.json（観測スキーマ）
用途: CapCut 側ドラフト生成の証跡
- `created_at`: string（ISO）
- `draft_name`: string
- `draft_path_ref?`: dict（PathRef; 推奨。`ssot/ops/OPS_PATHREF_CONVENTION.md`）
- `draft_path?`: string（legacy; ローカル環境の絶対パス。共有/UIの存在判定根拠にしない）
- `project_id`: string
- `template_used`: string
- `srt_file`: string
- `title`: string
- `transform`: dict
- `crossfade_sec`, `fade_duration_sec`: number

### 4.6 timeline_manifest.json（観測スキーマ / 診断用の“契約”）
用途:
- run_dir の入力/出力/依存を **1ファイルに固定**し、将来の `workspaces/` への移設・検証・UI表示の基礎にする。

生成:
- `packages/video_pipeline/tools/auto_capcut_run.py` が、`workspaces/audio/final` の SRT/WAV を解決できる場合に生成（失敗しても pipeline は止めない）。
- `packages/video_pipeline/tools/align_run_dir_to_tts_final.py` が retime 後に生成（strict validation）。

トップレベル（期待）:
- `schema`: `"ytm.timeline_manifest.v1"`
- `generated_at`: string（UTC ISO）
- `repo_root`: string（repo absolute path）
- `episode`: dict
- `source`: dict
- `derived`: dict
- `notes`: string

episode（期待）:
- `id`: `"CHxx-NNN"`
- `channel`: `"CHxx"`
- `video`: `"NNN"`

source.audio_wav（期待）:
- `path`: string（repo相対 or absolute）
- `sha1`: string
- `duration_sec`: number

source.audio_srt（期待）:
- `path`: string（repo相対 or absolute）
- `sha1`: string
- `end_sec`: number
- `entries`: number

derived（期待）:
- `run_dir`: string（repo相対）
- `image_cues`: dict
- `belt_config?`: dict（存在時のみ）
- `capcut_draft?`: dict（存在時のみ。PathRef推奨/legacy互換あり）

derived.capcut_draft（期待）:
- `path_ref?`: dict（PathRef; 推奨。`ssot/ops/OPS_PATHREF_CONVENTION.md`）
- `path?`: string（legacy; ローカル環境の絶対パス。共有/UIの存在判定根拠にしない）

derived.image_cues（期待）:
- `path`: string（run_dir 相対）
- `sha1`: string
- `count`: number
- `end_sec`: number
- `fps`: number
- `size`: dict
- `crossfade`: number
- `imgdur`: number

strict validation（期待ルール）:
- `audio_wav.duration_sec` と `audio_srt.end_sec` が許容差内（既定 tol=1s）
- `image_cues.end_sec` と `audio_srt.end_sec` が許容差内（既定 tol=1s）
- `images/0001.png ...` が cue 数ぶん存在（run_dir に images/ がある場合）

---

## 5. Thumbnails（サムネ）— projects.json

### 5.1 SoT
- `workspaces/thumbnails/projects.json`
 - テンプレ（型）SoT: `workspaces/thumbnails/templates.json`

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
- `image_path`: string（例: `CH26/001/00_thumb_1.png`, `CH01/002/002_calm.png`）
- `image_url`: string（外部URLを使う場合）

### 5.3 物理配置（重要）
- UI/Backend は `/thumbnails/assets/{image_path}` を配信する設計のため、物理ファイルは `workspaces/thumbnails/assets/{image_path}` に寄せる。
- 旧来の `workspaces/thumbnails/CHxx_<チャンネル名>/...` は Legacy 資産として移行/アーカイブ対象。

標準レイアウト（動画単位）:
- `workspaces/thumbnails/assets/{CH}/{NNN}/`
  - SoT（動画差分）:
    - 正本: `thumb_spec.<stable>.json`（例: `thumb_spec.00_thumb_1.json`, `thumb_spec.00_thumb_2.json`）
    - 互換: `thumb_spec.json`（legacy。2案運用時は混線事故の温床なので、stable へ移行する）
  - SoT（文字・行単位）:
    - 正本: `text_line_spec.<stable>.json`
    - 互換: `text_line_spec.json`（legacy。例外として `00_thumb_1` のみ fallback 可 / `00_thumb_2` は継承しない）
  - SoT（追加要素・図形/画像など）:
    - 正本: `elements_spec.<stable>.json`
    - 互換: `elements_spec.json`（legacy。例外として `00_thumb_1` のみ fallback 可 / `00_thumb_2` は継承しない）
  - 派生（planning由来）: `planning_meta.json`
  - 派生（安定出力）: `00_thumb.png` または `00_thumb_1.png` / `00_thumb_2.png`、`10_bg.png`、`20_portrait.png` など
  - 派生（build履歴）: `compiler/<build_id>/out_*.png`, `compiler/<build_id>/build_meta.json`

### 5.4 Layer Specs（画像レイヤ/文字レイヤの仕様YAML）
目的:
- 「画像レイヤ（背景生成の指示）」と「文字レイヤ（テキスト配置/デザイン）」を、**チャンネル固有の if 分岐を増やさず**に運用できるようにする。
- 仕様は汎用スキーマとして固定し、チャンネルごとの差分は YAML の値で吸収する（= 仕組みは共通、データだけ切替）。

配置（標準）:
- 仕様YAML（SoT）: `workspaces/thumbnails/compiler/layer_specs/*.yaml`
  - image prompts: `workspaces/thumbnails/compiler/layer_specs/image_prompts_v*.yaml`
  - text layout: `workspaces/thumbnails/compiler/layer_specs/text_layout_v*.yaml`

参照（例）:
- 旧/外部持ち込みの確定版: `CH10_image_prompts_FINAL_v3.yaml`, `CH10_text_layout_FINAL_v3.yaml`
- 取り込み後は `layer_specs/` 配下を正とし、`templates.json` から `layer_spec_ids` を参照して適用する。

スキーマ（要点）:
- image_prompts
  - `version`: int
  - `canvas`: `{w:int,h:int,aspect:str}`（主にアセット制作側の意図）
  - `policy`: dict（例: 左TSZ, forbid_text 等）
  - `items[]`: `{video_id,title?,person_key?,anchors?,prompt_ja}`（1動画=1背景指示）
- text_layout
  - `version`: int
  - `coordinate_system`: `normalized_0_to_1`
  - `global`: `safe_zones`, `fonts`, `effects_defaults`, `fit_rules`, `overlays`
    - `overlays.top_band` / `overlays.bottom_band`（省略可）:
      - 共通: `enabled: bool`, `color: "#RRGGBB"`, `y0/y1: float(0..1)`
      - 既定（gradient）: `alpha_top/alpha_bottom: float(0..1)`
      - `mode: brush|ink` の場合（黒筆帯/刷毛帯）:
        - `alpha: float(0..1)`（帯の最大不透明度）
        - `roughness: float`（エッジの歪み）
        - `feather_px: int`（エッジのフェード幅）
        - `hole_count: int`（かすれ/薄い箇所の密度）
        - `blur_px: int`（マスクのぼかし）
        - `seed: int`（省略可。未指定は安定seed）
  - `templates.*.slots.*.backdrop`（省略可。文字の背面に「筆のような帯」を敷く）:
    - `enabled: bool`
    - `mode: brush_stroke`（生成帯。互換: `brush`, `brushstroke`）
      - `color: "#RRGGBB"`
      - `alpha: float(0..1)`（帯の最大不透明度）
      - `pad_x_px/pad_y_px: int`（文字bboxに足す余白）
      - `roughness: float`（エッジの歪み）
      - `feather_px: int`（エッジのフェード幅）
      - `hole_count: int`（かすれ/薄い箇所の密度）
      - `blur_px: int`（マスクのぼかし）
      - `seed: int`（省略可。未指定は安定seed）
    - `mode: image|png|asset`（透過PNGを「文字レイヤの1つ下」に敷く。ベンチの黒筆帯に寄せたい時はこれ）
      - `image_path: str`（例: `asset/thumbnails/common/brush/brush_swipe_bench_02.png`）
      - `fit: stretch|cover|contain`（既定: `cover`）
      - `colorize: bool`（既定:false。trueの場合はPNGをalphaマスクとして `color` で塗る）
      - `color: "#RRGGBB"`（`colorize:true` の時に使用）
      - `alpha: float(0..1)`（PNG alpha に乗算）
      - `pad_x_px/pad_y_px: int`（文字bboxに足す余白）
  - `templates`: `{template_id: {slots, fallbacks}}`
  - `items[]`: `{video_id,title?,template_id,fallbacks?,text:{top,main,accent,author}}`

運用ルール:
- UI は企画CSVの `thumbnail_*` を正本として編集し、layer specs の既定値は初期提案として読み込める（オプション; 強制しない）。
- 文字合成（compose）は layer specs の `template_id/slots` を使ってもよいが、最終の文字列は企画CSV（`thumbnail_upper/title/lower` 等）を正とする。
- 画像生成（generate）は templates.json の `image_model_key` と prompt_template を正にし、layer specs の指示は「追加のガイド」として利用できる。

# OPS_THUMBNAILS_PIPELINE — サムネ量産/修正（ローカル合成）SSOT

目的:
- **サムネはCTRの主要因**なので、AI生成だけに寄せず、最終は **ローカル合成（Pillow）** で「崩れない・直せる・量産できる」を保証する。
- 「作る（量産）」と「直す（リテイク）」を同じ仕組みで回し、**全チャンネルで汎用的に**運用できるようにする。

このドキュメントは **Thumbnail Compiler（ローカル合成）** の運用正本。  
UI（`/thumbnails`）の管理SoTや、AI画像生成テンプレの管理SoTと整合させる。

関連:
- `plans/PLAN_OPS_PERFORMANCE_BOTTLENECKS.md`（遅い/詰まる課題の観測→DoD付き改善）
- `plans/PLAN_THUMBNAILS_SCALE_SYSTEM.md`（サムネのSoT/生成物/運用をスケールさせる計画）

---

## 1. SoT（正本）と役割分担

### 1.1 管理SoT（UIが読む/書く）
- **Projects SoT**: `workspaces/thumbnails/projects.json`
  - 各チャンネル/各動画のサムネ案（variants）と `status/notes` を追跡。
  - `variants[].tags` はUI運用の最小拡張に使う。
    - `rejected`: 不採用（ボツ）フラグ。UI（`/thumbnails`）でチェックを付けて管理する（2案でも**片方だけ**に付けられる）。
- **Templates SoT**: `workspaces/thumbnails/templates.json`
  - AI生成テンプレ（prompt/model）と、チャンネル別の設定（layer_specs 等）を管理。
  - `templates[].image_model_key` は背景生成に使う ImageClient の **model selector**（model key もしくは slot code）。
    - model key 正本: `configs/image_models.yaml: models.*`
    - slot code 正本: `configs/image_model_slots.yaml`（例: `f-4`）
    - 運用既定（現行）: **Gemini 2.5 Flash Image**（`g-1` / `img-gemini-flash-1`）
      - ポリシー: サムネは **Gemini > FLUX max**（サイレント切替はしない）
      - 例外（許可）: サムネ背景生成（`thumbnail_image_gen`）に限り **Gemini 3**（例: `gemini_3_pro_image_preview`）の利用を許可する（必要時のみ明示して使う）。
        - 注意: 動画内画像（`visual_image_gen`）では Gemini 3 は禁止（別SSOT: `ssot/ops/OPS_CHANNEL_MODEL_ROUTING.md`）。
  - Fireworks（画像）キー運用（固定）:
    - 台本用（`FIREWORKS_SCRIPT*`）とは **別プール**（`FIREWORKS_IMAGE*`）で運用する（コスト/枯渇の混線防止）
      - 現行運用では **Fireworks（text/台本）は有効**（`YTM_DISABLE_FIREWORKS_TEXT=0`）。無効化はデバッグ時のみ（`YTM_EMERGENCY_OVERRIDE=1`）。
    - キーローテ（任意・推奨）: `~/.ytm/secrets/fireworks_image_keys.txt`
      - 追加/整形: `python3 scripts/ops/fireworks_keyring.py --pool image add --key ...`（キーは表示しない）
      - token-free状態更新: `python3 scripts/ops/fireworks_keyring.py --pool image check --show-masked`
    - 並列運用: 同一キーの同時利用を避けるため、画像生成は **key lease** で排他する（`FIREWORKS_KEYS_LEASE_DIR` 参照）

    - 非常時（Gemini が使えない/止める必要がある場合）:
      - **サイレント切替は禁止**（正本: `ssot/DECISIONS.md:D-002`）。切替は必ず明示する。
    - サムネ背景生成だけ FLUX max に切替する場合は、タスク強制で固定する:
      - 例: `IMAGE_CLIENT_FORCE_MODEL_KEY_THUMBNAIL_IMAGE_GEN=f-4 python3 scripts/thumbnails/build.py build --channel CH01 --engine layer_specs --videos 257 --regen-bg --force`
    - サムネ背景生成で Gemini 3 を使う場合（許可・明示）:
      - 例: `IMAGE_CLIENT_FORCE_MODEL_KEY_THUMBNAIL_IMAGE_GEN=gemini_3_pro_image_preview python3 scripts/thumbnails/build.py build --channel CH01 --engine layer_specs --videos 257 --regen-bg --force`
    - 事故防止のため `allow_fallback` は有効化しない（明示 `model_key` は strict が原則）。
    - 期間が長い場合は `.gitignore` 対象の `configs/*.local.*`（例: `configs/image_models.local.yaml`, `configs/image_model_slots.local.yaml`）で切替してよい（コミットしない）。

### 1.2 ローカル合成（Compiler）SoT
- Stylepack（組版スタイル）: `workspaces/thumbnails/compiler/stylepacks/*.yaml`
- Policy（バリアント方針）: `workspaces/thumbnails/compiler/policies/*.yaml`（将来拡張の入口）
- Layer Specs（テキスト配置/背景プロンプトの定義）:
  - `workspaces/thumbnails/compiler/layer_specs/*.yaml`
  - レジストリ: `workspaces/thumbnails/templates.json: layer_specs.registry`

---

## 2. “サムネ作成”を分解する（全チャンネル共通）

### 2.1 背景（素材）を用意する
背景は原則、動画ごとに `workspaces/thumbnails/assets/{CH}/{NNN}/10_bg.png`（または jpg/webp）を用意する。

背景の作り方は2通り:
1) UIのテンプレからAI生成（Templates SoTに従う）
2) 手動/外部で作った画像を配置（アップロード/コピー）

手動差し替え（推奨: UI経由）:
- `/thumbnails` → `調整（ドラッグ）` → `素材の差し替え（画像アップロード）` からアップロードすると、安定ファイル名へ置換される。
  - 背景: `10_bg.png`
  - 肖像（任意）: `20_portrait.png`
  - 出力（最終）: `00_thumb.png`（または `00_thumb_1.png` / `00_thumb_2.png`）
- 直接ファイル操作する場合も、保存先は同じ: `workspaces/thumbnails/assets/{CH}/{NNN}/`

### 2.2 文字（コピー）を用意する
コピーのSoTは「企画CSV」または「Layer Specs」。
- 企画CSV（推奨フィールド）:
  - `サムネタイトル上` / `サムネタイトル` / `サムネタイトル下`
  - `サムネ画像プロンプト（URL・テキスト指示込み）`
- Layer Specs:
  - `text_layout_v3.yaml`（動画ごとの文字/強調/配置）
  - `image_prompts_v3.yaml`（背景プロンプト）

### 2.3 ローカル合成で“最終サムネ”を作る
背景（10_bg.*） + 文字（layout/spec）を **ローカル合成**し、最終PNGを生成する。

出力は運用により2系統:
- `00_thumb.png`（“ファイル直参照”運用。CH10など）
- `compiler/<build_id>/out_01.png`（“build単位”運用。CH07など）

補足（2案/複数安定出力）:
- 原則: サムネの“安定出力”は `00_thumb.png` の1枚運用。
- 2案（`00_thumb_1/2`）を運用する場合は、各動画あたり **2つの別物**として扱う（片方の調整がもう片方に影響しないこと）。
  - UI（`/thumbnails`）のギャラリーは `2案（00_thumb_1/2）` 表示で確認する。未生成の案は `未生成` として表示される（生成/調整の入口）。
  - 企画CSV一覧（`/planning` の `サムネ` 列）は、`00_thumb_1/00_thumb_2` がある場合 **2枚を並べて表示**し、それぞれクリックで `stable=00_thumb_1|00_thumb_2` を付けて編集画面を開く（混線防止）。
  - UI（調整モーダル）のボタン意味:
    - `保存（設定のみ）`: `thumb_spec.<stable>.json` / `text_line_spec.<stable>.json` / `elements_spec.<stable>.json` を保存（画像PNGは更新しない）
    - `保存して再生成（PNG更新）`: 上記を保存した上で `00_thumb_<n>.png` を再生成して反映する
  - 例: CH26 は `00_thumb_1/00_thumb_2` を前提に運用する（30枚×2=60枚）。
- `assets/{CH}/{NNN}/00_thumb_1.png`, `00_thumb_2.png` のように、複数の安定出力を置いてよい（命名は運用で統一する）。
- 追跡は `projects.json: projects[].variants` に登録し、UI（`/thumbnails`）のギャラリーで `2案（00_thumb_1/2）` または `全バリアント` 表示で確認する。
- “安定出力が複数ある”場合は、**調整（tuning）のSoTも出力ごとに分離**する（混線禁止）。
  - 互換ルール（重要）:
    - 旧フォーマット（`thumb_spec.json`, `text_line_spec.json`, `elements_spec.json`）は当面残る。
    - **暗黙fallbackは禁止**（混線事故の温床）。例外として **`00_thumb_1` のみ**旧ファイルへ fallback してよい。
    - `00_thumb_2` は旧ファイルを **絶対に継承しない**（存在しない場合は “空” として扱う）。
  - `assets/{CH}/{NNN}/thumb_spec.<stable>.json`（例: `thumb_spec.00_thumb_1.json`, `thumb_spec.00_thumb_2.json`）
    - schema: `ytm.thumbnail.thumb_spec.v1`（中身は通常の `thumb_spec.json` と同じ）
    - 背景/肖像/文字effects/template選択などの “leaf overrides” を安定出力ごとに独立保持する。
    - 重要: 2案の混線防止のため、**`00_thumb_2` の既定は `overrides.portrait.enabled=false`**（必要なら `thumb_spec.00_thumb_2.json` で明示的にON）。
      - CH26 は背景に顔が含まれることがあるため、`overrides.portrait.enabled=true` の間は “背景の顔を抑制” (`overrides.portrait.suppress_bg`) を強制ON（ダブルフェイス事故防止）。
        - 抑制領域は `overrides.portrait.offset_(x|y)` に追従しつつ、**元位置とオフセット位置の両方を覆う**（UIプレビュー/ビルド両方）。
  - `assets/{CH}/{NNN}/text_line_spec.<stable>.json`（例: `text_line_spec.00_thumb_1.json`）
    - schema: `ytm.thumbnail.text_line_spec.v1`
    - **文字を行（slot）単位**で “位置/拡大縮小/回転” を保持する（Canva寄せのため）。
  - `assets/{CH}/{NNN}/elements_spec.<stable>.json`（例: `elements_spec.00_thumb_1.json`）
    - schema: `ytm.thumbnail.elements_spec.v1`
    - 図形/画像などの追加要素を保持する（Canva基本機能に寄せる）。
    - layer: `below_portrait|above_portrait`, かつ z で整列し、**最終は文字が最前面**（画像の上に文字）。

---

## 3. Compiler Engine（汎用化の核）

チャンネルごとに「どの合成エンジンを使うか」を切替できるようにする。

### 3.1 Engine A: `layer_specs_v3`（推奨・柔軟）
- 背景: `10_bg.png`（動画ごと）
- 文字: `workspaces/thumbnails/compiler/layer_specs/text_layout_v3.yaml`
- 特徴:
  - チャンネル/動画ごとに **レイアウトを細かく制御**できる
  - 一括で明るさ補正（brightness/contrast/gamma）してCTRを上げやすい

### 3.2 Engine B: `buddha_3line_v1`（既存・互換）
- Stylepackに従って3段コピー（赤/黄/白）を合成する。
- 既存の `workspaces/thumbnails/compiler/stylepacks/CHxx_buddha_left_3line_v1.yaml` を利用。

### 3.3 Engine Auto（標準動作）
- `templates.json.channels[CHxx].layer_specs` があれば `layer_specs_v3`
- なければ Stylepack を探索して `buddha_3line_v1`
- どちらも無ければエラー（設定不足）

### 3.4 チャンネル別の既定値（Templates SoT）
CLI の “毎回同じ指定” を減らすため、`templates.json` の各チャンネルに `compiler_defaults` を任意で持たせる。

例（任意・後方互換）:
```json
{
  "channels": {
    "CH10": {
      "compiler_defaults": {
        "bg_enhance": { "brightness": 1.30, "contrast": 1.10, "color": 1.06, "gamma": 0.88 },
        "qc": { "tile_w": 640, "tile_h": 360, "cols": 6, "pad": 8 }
      }
    }
  }
}
```

ルール:
- CLI の引数が “既定値（例: brightness=1.0）” のままなら、`compiler_defaults` を適用してよい。
- 明示指定があれば CLI を優先する（SSOT上の原則: “手動指定が最強”）。

### 3.5 型管理（Typed Specs）とレイヤ分離（必須設計）
サムネ量産/修正を「全チャンネルでスケール」させるため、Compiler は **型（schema）とレイヤ**を明確に分離する。

原則:
- **Image Layer（画像レイヤ）** と **Text Layer（文字レイヤ）** を別モジュールに分ける（責務の混在を禁止）。
- `layer_specs_v3` の YAML は **Typed schema で読み込み時に検証**し、壊れた入力を早期に落とす（dict丸投げ禁止）。

役割:
- Image Layer:
  - 入力: `assets/{CH}/{NNN}/10_bg.*` + `bg_enhance` + canvas
  - 出力: **必ず canvas サイズに正規化された背景**（16:9の crop/resize を含む）
  - ルール: 元画像は上書きしない（合成時に一時PNG化）
- Text Layer:
  - 入力: 正規化済み背景 + `text_layout_v3`（動画ごとのテキスト/テンプレ）
  - 出力: 最終PNG
  - ルール: 背景の明るさ/サイズ変更をしない（画像は Image Layer の責務）

実装の入口（参照先）:
- Typed schema: `packages/script_pipeline/thumbnails/compiler/layer_specs_schema_v3.py`
- Typed loader: `packages/script_pipeline/thumbnails/compiler/layer_specs.py`
- Image Layer: `packages/script_pipeline/thumbnails/layers/image_layer.py`
- Text Layer: `packages/script_pipeline/thumbnails/layers/text_layer.py`

#### 3.5.1 行内の色分け（タグ・最小拡張）
メモ9の「黄/赤の部分強調」を安定運用するため、Text Layer は **行内タグ**を解釈できる（solid fill のみ / 1行出力時のみ）。

- 記法: `[y]...[/y]`（黄） / `[r]...[/r]`（赤） / `[w]...[/w]`（白）
- 実装: `packages/script_pipeline/thumbnails/compiler/compose_text_layout.py`
- 注意: 自動折り返しが入るケース（複数行）はタグを無視して通常描画する（崩れ防止）

#### 3.5.2 Planning CSV からのコピー注入（空欄のみ）
`layer_specs_v3` の `items[].text` を空欄にした場合、build 時に Planning CSV のコピーで補完できる（既存specを壊さないため **空欄のみ**）。

- 対応列: `サムネタイトル上` / `サムネタイトル` / `サムネタイトル下`
- 3行まとめ運用（CH01想定）: `サムネタイトル` に `\\n` 区切りで 3行を入れてもよい
- 実装: `packages/script_pipeline/thumbnails/tools/layer_specs_builder.py`

---

## 4. “量産”と“修正（リテイク）”を同じ入口にする

### 4.1 量産（build）
- 対象動画を指定してまとめて合成する。
- 典型用途:
  - 背景生成後の「文字合成のみ」一括反映
  - stylepack/layer_specs変更後の一括再出力

### 4.2 修正（retake）
UI/SoT側で「やり直しフラグ」を立て、CLIで一括再合成して戻す。

運用SoT:
- `workspaces/thumbnails/projects.json: projects[].status`
  - `in_progress` = リテイク対象（やり直し）
  - `review` = 修正完了（レビュー待ち）
- `projects[].notes` = リテイク理由と修正内容（人間が見て分かる形で残す）

**重要**: 修正完了後は `status` を `review` に戻し、`notes` に `修正済み:` 行で「何を変えたか（パラメータ/日時）」を残す。

---

## 5. 明るさ補正（CTR重視の標準機能）

背景補正パラメータ（全部 “背景だけ” に適用）:
- `brightness`（>1 で明るく）
- `contrast`（>1 でメリハリ）
- `color`（>1 で彩度）
- `gamma`（<1 で明るく。暗部を持ち上げやすい）

原則:
- 背景画像を直接上書きしない（累積補正を避けるため、合成時に一時PNGを作る）
- “やりすぎ”は白飛び/ノイズで逆効果。QCで一括確認してから反映する。

---

## 6. QC（コンタクトシート）

ローカル合成は **一括目視**できる状態が最重要。
- `workspaces/thumbnails/assets/{CH}/_qc/contactsheet_*.png` を生成し、差分/劣化を素早く検出する。
- UIでの確認を容易にするため、最新のQCは `workspaces/thumbnails/assets/{CH}/library/qc/contactsheet.png` に publish する（正本はここ）。
  - UI: `/thumbnails` → **QCタブ**
- 例外（テンプレ/ベンチの“参考集”を残したい時）:
  - `workspaces/thumbnails/assets/{CH}/library/qc/` に **意図的に** `qc__YYYYMMDD__<topic>__<variant>.png` のような命名で少数だけ置いてよい（探索ノイズを増やさない）。
  - 生成元は `--source-name 00_thumb_sample_*.png` のような“安定出力名”を使い、本番 `00_thumb.png` を上書きしない。

---

## 7. 入口（確定CLI）

統一CLI（推奨）:
- `python scripts/thumbnails/build.py --help`

想定サブコマンド:
- `build`: 指定動画を合成（量産）
- `retake`: `projects.json` の `status=in_progress` を対象に合成し、完了後 `review` に戻して証跡を残す
- `qc`: 指定動画のサムネからコンタクトシートを生成

※ 本CLIは “全チャンネルで同じ操作感” を最優先にする（チャンネル固有の例外は config に寄せる）。

A/B（2案）で安定出力名を分けたい場合:
- `build` に `--thumb-name`（例: `00_thumb_1.png`, `00_thumb_2.png`）を指定して `assets/{CH}/{NNN}/` 配下へ出力する。
- `--variant-label` を指定すると `projects.json` の variant label を上書きできる（未指定時は自動で決まる）。

---

## 8. チャンネル追加/移行の手順（標準）

1) UIで `projects.json` に対象動画を登録（または既存を利用）
2) 背景運用を決める
   - 生成するなら `templates.json` にテンプレを追加
   - 既存素材なら `assets/{CH}/{NNN}/10_bg.png` を用意
3) 合成エンジンを決める
   - 推奨: `layer_specs_v3`（`templates.json.channels[CHxx].layer_specs` を設定）
   - 互換: `buddha_3line_v1`（stylepackを用意）
4) （任意）ベンチマーク（競合）サムネの特徴を集約→テンプレ雛形を作る
   - 競合定義SoT: `packages/script_pipeline/channels/CHxx-*/channel_info.json: benchmarks.channels`
   - 収集: `python3 scripts/ops/yt_dlp_benchmark_analyze.py --channel CHxx --apply`
   - 特徴抽出（styleguide）: `python3 scripts/ops/thumbnail_styleguide.py build --handle @HANDLE --apply`（または `--channel-id UC...`）
   - 雛形生成（layer_specs + templates.json追記）: `python3 scripts/ops/thumbnail_styleguide.py scaffold --handle @HANDLE --channel-code CHxx --apply`
     - 出力: `workspaces/research/thumbnail_styleguides/<UC...>/styleguide.{json,md}`
5) `scripts/thumbnails/build.py build ...` で量産 → QC → UIでレビュー

---

## 9. 課題（処理が遅い/カオス化する原因の確定メモ）

ここは **憶測を書かない**（観測できた事実 + 影響 + 直す入口だけを書く）。  
改善タスクは `ssot/ops/OPS_GLOBAL_TODO.md` に起票して追う。

### 9.1 CH22: `bg_pan_zoom` が混在している（フレーミングが毎回変わる）

- 観測（legacy / 2025-12-29時点）: `workspaces/thumbnails/assets/CH22/*/meta.json:bg_pan_zoom` に以下の **3パターンが混在**
  - `(zoom=1.0, pan_y=0.0)` → `001,003,004,007,008,009,010,013,015,016,017,018,021`
  - `(zoom=1.2, pan_y=-1.0)` → `002,005,006,011,014,019,020,022,023,024,025,026,027,028,029,030`
  - `(zoom=1.15, pan_y=-0.6)` → `012`
- 影響: 「画像をもっと下に」「文字と顔が被ってる」系の修正が、動画ごとに効いたり効かなかったりして収束しない（レビューが地獄化）。
- 原因（legacy / 当時の確定）:
  - `scripts/thumbnails/build.py:_apply_compiler_defaults()` は `bg_enhance` は適用するが、`bg_pan_zoom` は適用しない。
  - `packages/script_pipeline/thumbnails/tools/layer_specs_builder.py` は `bg_zoom/bg_pan_*` を **CLI引数からのみ**受け取り、チャンネル別SoTが無い。
- 現行（対応済 / 2025-12-30）:
  - チャンネル既定: `templates.json.channels[CHxx].compiler_defaults.bg_pan_zoom` を読み、未指定（既定値）の場合に適用する。
  - 動画差分: `workspaces/thumbnails/assets/{CH}/{NNN}/thumb_spec.<stable>.json: overrides.bg_pan_zoom.*` に寄せる（legacy: `thumb_spec.json`）。
  - build履歴メタは `compiler/<build_id>/build_meta.json` に分離（legacy `meta.json` は上書きされない）。

確認ワンライナー（混在チェック）:
`python - <<'PY'\nimport json\nfrom pathlib import Path\nroot=Path('workspaces/thumbnails/assets/CH22')\ncombos={}\nfor vid_dir in sorted(root.iterdir()):\n  if not vid_dir.is_dir() or not vid_dir.name.isdigit():\n    continue\n  metas=sorted(vid_dir.glob('compiler/*/build_meta.json'), key=lambda p: p.stat().st_mtime)\n  if not metas:\n    continue\n  p=metas[-1]\n  pan=json.loads(p.read_text(encoding='utf-8')).get('bg_pan_zoom',{})\n  k=(pan.get('zoom'),pan.get('pan_x'),pan.get('pan_y'))\n  combos.setdefault(k,[]).append(vid_dir.name)\nfor k,vids in sorted(combos.items(), key=lambda kv: str(kv[0])):\n  print(k, len(vids), vids)\nPY`

### 9.2 CH22: Layer Specs がコピーを保持していない（Planning CSV 依存が強い）

- 観測: `workspaces/thumbnails/compiler/layer_specs/ch22_text_layout_ch22_v1.yaml` の `items[].text` が空で、build時に planning CSV の `サムネタイトル上/サムネタイトル/サムネタイトル下` を **空欄のみ注入**する設計（`packages/script_pipeline/thumbnails/tools/layer_specs_builder.py:_load_planning_copy()`）。
- 影響: 「どのコピーがSoTか」が見えにくく、CSV側の未更新/空欄があると、thumbが空文字になって原因切り分けに時間が掛かる。
- 現行（対応済 / 2025-12-30）: 動画差分のコピー例外は `thumb_spec.<stable>.json: overrides.copy_override.{upper,title,lower}` で上書き可能（legacy: `thumb_spec.json`。CSVの空欄事故を局所化する）。

### 9.3 build が Planning CSV を動画ごとに都度ロードしている（バッチが遅い）

- 観測（legacy）: `packages/script_pipeline/thumbnails/tools/layer_specs_builder.py:_load_planning_copy()` が `planning_store.get_rows(... force_refresh=True)` を **動画ごとに**呼ぶ。
- 影響: 30本などのバッチ合成で同じCSVを何度も読み、処理が遅くなる（I/O無駄）。
- 現行（対応済 / 2025-12-30）: `planning_store` に mtime/size ベースのキャッシュを追加し、build実行単位で共有する。

### 9.4 “作業メモ/抽出物” の置き場が揺れて「消えた」に見える

- 観測: `log_research/` は scratch で、収束後 `backups/_incident_archives/**/log_research/` に退避され、repo直下には残さない（`log_research/README.md`）。
- 影響: `log_research/` に置いた復元メモ/抽出ファイルは後で参照できず「消えた」事故になる。恒久情報は SSOT / `workspaces/logs/` / `projects.json:notes` に置く。

### 9.5 チャンネル固有の “フレーミング調整” の SoT が無い

- 観測（legacy）: CH26は `workspaces/thumbnails/compiler/policies/ch26_portrait_overrides_v1.yaml` のように per-video override の入口があるが、CH22の `bg_pan_zoom` は同等のSoT入口が無い。
- 影響: 「画像を下に」等の指示が、毎回 CLI の手動パラメータに依存して混在し、結果がカオス化する。
- 現行（対応済 / 2025-12-30）:
  - チャンネル既定は `templates.json.channels[CHxx].compiler_defaults`（bg_pan_zoom/bg_enhance/bg_enhance_band）へ寄せる。
  - 動画差分は `thumb_spec.<stable>.json`（overrides.*）へ寄せる（legacy: `thumb_spec.json`）。

---

## 9. 現状の課題（速度/安定性）

このパイプラインは「崩れない・直せる」を優先している一方で、現状の実装/運用だと **大量リテイク時に時間がかかりすぎる** / **PNG破損で止まる** 事故が起きやすい。

### 9.1 PNG書き込みが重い（`optimize=True`）

- 症状: `build/retake/qc` が数十本規模で数分〜十数分かかる（反復修正に不向き）
- 主因: Pillow の `save(..., optimize=True)` が CPU/IO を食う
  - `packages/script_pipeline/thumbnails/layers/image_layer.py: crop_resize_to_16x9`
  - `packages/script_pipeline/thumbnails/layers/image_layer.py: enhanced_bg_path`
  - `packages/script_pipeline/thumbnails/layers/text_layer.py: compose_text_to_png`
  - `scripts/thumbnails/build.py: build_contactsheet`
- 対応（P0 / 対応済）:
  - `scripts/thumbnails/build.py` に `--output-mode {draft,final}` を追加（draft: optimize=False, compress_level=1 / final: optimize=True, compress_level=6）

### 9.2 非アトミック上書き → PNG破損（truncated）

- 症状: `OSError: image file is truncated` でビルドが停止し、`10_bg.png` / `00_thumb.png` が壊れて再作業になる
- 主因:
  - 画像保存が **直接destへ上書き**（中断/同時実行/長時間書き込みで部分ファイルが残る）
  - `optimize=True` が書き込み時間を伸ばし、破損リスクを増幅
- 対応（P0 / 対応済）:
  - `save_png_atomic()`（tmp→replace + verify）を導入し、サムネ生成のPNG保存箇所へ適用する

### 9.3 無駄な再読込（Planning/Fonts）

- 症状: 1枚あたりの合成が遅い（小さな変更でも待ちが発生）
- 主因:
  - Planning CSV を動画ごとに `force_refresh=True` で読んでいる（`packages/script_pipeline/thumbnails/tools/layer_specs_builder.py:_load_planning_copy`）
  - フォントロードが重い（`PIL.ImageFont.truetype`）
- 対応（P1 / 対応済）:
  - Planning rows は build 実行単位で 1 回ロードして共有（キャッシュ）する
  - フォントロードは LRU でキャッシュする（対応済み: `packages/script_pipeline/thumbnails/compiler/compose_text_layout.py:_load_truetype`）

### 9.4 「手動背景」チャンネル（例: CH22）のリテイクが止まりやすい

- 症状: 「背景が間違い/要再生成」系のコメント対応が、背景差し替え導線の弱さで止まりやすい
- 対応案（P1）:
  - `templates.json` に「作業用の生成テンプレ」を追加し、必要時だけ `--regen-bg` で作り直せる運用を定義する
  - もしくは `image_prompts_v3.yaml` を全動画分まで拡充し、背景生成の入口を整備する

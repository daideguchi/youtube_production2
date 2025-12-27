# OPS_THUMBNAILS_PIPELINE — サムネ量産/修正（ローカル合成）SSOT

目的:
- **サムネはCTRの主要因**なので、AI生成だけに寄せず、最終は **ローカル合成（Pillow）** で「崩れない・直せる・量産できる」を保証する。
- 「作る（量産）」と「直す（リテイク）」を同じ仕組みで回し、**全チャンネルで汎用的に**運用できるようにする。

このドキュメントは **Thumbnail Compiler（ローカル合成）** の運用正本。  
UI（`/thumbnails`）の管理SoTや、AI画像生成テンプレの管理SoTと整合させる。

---

## 1. SoT（正本）と役割分担

### 1.1 管理SoT（UIが読む/書く）
- **Projects SoT**: `workspaces/thumbnails/projects.json`
  - 各チャンネル/各動画のサムネ案（variants）と `status/notes` を追跡。
- **Templates SoT**: `workspaces/thumbnails/templates.json`
  - AI生成テンプレ（prompt/model）と、チャンネル別の設定（layer_specs 等）を管理。

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

---

## 7. 入口（確定CLI）

統一CLI（推奨）:
- `python scripts/thumbnails/build.py --help`

想定サブコマンド:
- `build`: 指定動画を合成（量産）
- `retake`: `projects.json` の `status=in_progress` を対象に合成し、完了後 `review` に戻して証跡を残す
- `qc`: 指定動画のサムネからコンタクトシートを生成

※ 本CLIは “全チャンネルで同じ操作感” を最優先にする（チャンネル固有の例外は config に寄せる）。

---

## 8. チャンネル追加/移行の手順（標準）

1) UIで `projects.json` に対象動画を登録（または既存を利用）
2) 背景運用を決める
   - 生成するなら `templates.json` にテンプレを追加
   - 既存素材なら `assets/{CH}/{NNN}/10_bg.png` を用意
3) 合成エンジンを決める
   - 推奨: `layer_specs_v3`（`templates.json.channels[CHxx].layer_specs` を設定）
   - 互換: `buddha_3line_v1`（stylepackを用意）
4) `scripts/thumbnails/build.py build ...` で量産 → QC → UIでレビュー

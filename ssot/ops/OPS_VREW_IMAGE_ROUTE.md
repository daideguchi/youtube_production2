# OPS_VREW_IMAGE_ROUTE (SSOT)

- 最終更新日: 2025-12-30
- 目的: **Vrewで量産した画像**を、既存の **CapCutドラフトの画像キュー**へ安全に差し込むための別ルートを確定する。
- 適用範囲: `packages/video_pipeline/tools/*`（Vrew用プロンプト生成 / 画像取り込み / CapCut差し替え）。

---

## 1. 全体像（3ステップ）

1) 台本（txt）/字幕（srt）→ **Vrewインポート用プロンプト本文**を生成  
2) Vrewで画像生成 → 生成画像を `images/` に揃えてマニフェスト更新  
3) `image_manifest.json` をSoTとして、CapCutドラフトの画像スロットへ差し替え

このルートは既存の `srt2images`（Gemini直生成）と競合しない。  
CapCut側のトラック構造/タイムレンジは壊さず、**画像素材のみ**を置換する。

---

## 2. SoT（唯一の真実）

- SoT: `image_manifest.json`
  - 画像生成/再実行/差し替えは、常にこのマニフェストを読み書きする。
- 画像ファイルの正本: `images/` 配下（マニフェストの `segments[*].image_path` で参照）

---

## 3. Runディレクトリ（推奨構成）

任意の作業ディレクトリ（例: `workspaces/video/runs/<run_name>/vrew_route/`）に以下を置く:

- `vrew_import_prompts.txt`
- `image_manifest.json`
- `style_preset.json`（任意）
- `images/`
  - `img_0001.png`（固定: ゼロ埋め4桁、queue_index=1始まり）
- `logs/`
  - `run_YYYYMMDD_HHMMSS.jsonl`（1行1イベント）

---

## 4. 入力/出力仕様（確定）

### 4.1 入力

- `--source srt`
  - `script.srt`（字幕ブロック=1セグメント）
- `--source txt`
  - `script.txt`（文単位=1セグメント）
- `style_preset.json`（任意）

### 4.2 出力

1) `vrew_import_prompts.txt`
   - 1行=1プロンプト
   - **各行末尾は必ず `。`**
   - **行中に `。` を含まない**（内部句点はサニタイズ）
2) `image_manifest.json`
3) `images/img_{queue_index:04d}.{ext}`
4) CapCutドラフト（既存ドラフトを更新）

---

## 5. style_preset.json（確定スキーマ）

```json
{
  "style_prefix": "string",
  "constraints": "string",
  "banned_terms": ["string"],
  "default_duration_ms": 5000,
  "image_spec": { "width": 1920, "height": 1080, "format": "png" }
}
```

- `style_prefix` / `constraints` は将来差し替え可能にする（固定化しない）。
- `default_duration_ms` は `source=txt` の `start_ms/end_ms` 補完に使用する。

---

## 6. image_manifest.json（確定スキーマ）

```json
{
  "project_id": "string",
  "source_type": "txt|srt",
  "image_spec": { "width": 1920, "height": 1080, "format": "png|jpg|webp" },
  "segments": [
    {
      "queue_index": 1,
      "segment_id": "seg_0001",
      "start_ms": 0,
      "end_ms": 4500,
      "source_text": "string",
      "prompt": "string",
      "prompt_hash": "sha256hex",
      "image_path": "images/img_0001.png",
      "status": "pending|generated|placed|failed",
      "error": null
    }
  ]
}
```

- `queue_index`: **1始まり**（固定）
- `source=srt`: SRTタイムコード→msで必ず埋める
- `source=txt`: `default_duration_ms` で補完

---

## 7. プロンプト生成ルール（Vrew本文用）

### 7.1 テンプレ（1プロンプト=1文）

`{style_prefix}{scene}{constraints}。`

- `scene` は入力テキストを短く整形したもの（初期実装は決定論・LLM任意）。
- 固有名詞/著作権リスク語は避ける方針（運用で `banned_terms` に追加）。

### 7.2 サニタイズ（重要）

- 文末は `。` に統一
- 行中の `。` は `、` に置換（末尾のみ `。`）
- `！` `？` などは行中に残さない（置換/除去）
- `。。` などの連続終端は1つに縮退

### 7.3 バリデーション（既定）

- `endswith("。")` 必須
- 行中に `。` が存在したらNG（末尾以外）
- 文字数: min=20 / max=220（上書き可）
- 禁止語: `banned_terms` を含む場合はNG

NG時は（設定で）「修正して継続」または「failedにして除外」を選べる。

---

## 8. Vrew生成画像の取り込み（確定）

- Vrewから書き出した画像ディレクトリを入力にし、以下を実行:
  - `segments[*].queue_index` に対応する `images/img_XXXX.ext` にコピー/リネーム
  - `status=generated` に更新
  - 欠損は `failed`（全停止しない）

ファイル名に `0001` 等が含まれる場合は番号で対応し、含まれない場合は自然順（名前ソート）で対応する。

---

## 9. CapCut差し替え（確定方針）

### 9.1 方式（採用）

- 方式B（並び順）を採用し、既存パイプラインの慣例に合わせる:
  - `draft_content.json` の `tracks` から `name/id` が `srt2images_` で始まる動画トラックを優先
  - fallback: manifest件数とセグメント数が一致する動画トラック

### 9.2 キャッシュ無効化（必須）

- CapCutは素材UUIDでキャッシュするため、差し替えは **新UUIDを発行して参照を置換**する（IDスワップ）。

### 9.3 draft_info 同期（必須）

- 置換後に `tools/sync_srt2images_materials.py` で `draft_info.json` を同期（トラック構造/タイムレンジは不変）。

### 9.4 失敗時挙動（止まらない）

- 画像欠損 / スロット特定失敗 / JSON不正:
  - 当該 `queue_index` を `failed` にして継続
  - `error` に理由を格納

---

## 10. CLI（確定）

このリポジトリでは `python3 -m video_pipeline.tools.<tool>` も同等。

1) プロンプト+マニフェスト生成

```bash
generate-vrew-prompts --source srt --in script.srt --outdir <run_dir>
```

2) 画像取り込み（Vrew出力→images/へ整列）

```bash
render-images --manifest <run_dir>/image_manifest.json --from-vrew <vrew_export_dir>
```

3) CapCutへ配置（画像差し替え）

```bash
place-images-to-capcut --manifest <run_dir>/image_manifest.json --draft <capcut_draft_dir> --apply
```

---

## 11. 受け入れ基準（テスト観点）

1) `vrew_import_prompts.txt` が全行末尾 `。`、行中に句点なし
2) `image_manifest.json` の segments件数 == プロンプト行数
3) `images/img_XXXX.*` が揃い、欠損は `failed` として検出できる
4) CapCutドラフト上で queue_index順に画像参照が差し替わる
5) 再実行してもトラック増殖や参照崩れが起きない（冪等）

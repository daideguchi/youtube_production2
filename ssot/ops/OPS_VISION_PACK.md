# OPS_VISION_PACK — スクショ/サムネの読み取り精度を上げる（任意ツール）

目的:
- 「スクショをそのまま投げる」運用で起きがちな誤読（小さい文字/低コントラスト/ノイズ）を減らす。
- 画像を **複数の前処理版**（拡大/シャープ/二値化/エッジ）と **自動切り出し（テキスト領域候補）** に分解し、LLM入力を強くする。

これは **任意ツール**（既存パイプラインのSoTは変更しない）。  
出力は `workspaces/tmp/` 配下を既定とし、作業後にディレクトリごと削除してよい（L3扱い）。

関連:
- Entry points: `ssot/ops/OPS_ENTRYPOINTS_INDEX.md`
- 生成物ライフサイクル: `ssot/plans/PLAN_OPS_ARTIFACT_LIFECYCLE.md`

---

## 1) 入口（CLI）

実装:
- `scripts/vision/vision_pack.py`

### 1.1 スクショ（UIテキスト転記向け）
```bash
./scripts/with_ytm_env.sh python3 scripts/vision/vision_pack.py screenshot /path/to/screenshot.png
```

OCRも併用（best-effort）:
```bash
./scripts/with_ytm_env.sh python3 scripts/vision/vision_pack.py screenshot /path/to/screenshot.png --ocr --ocr-lang jpn+eng
```

### 1.2 サムネ（配色/構図の解析向け）
```bash
./scripts/with_ytm_env.sh python3 scripts/vision/vision_pack.py thumbnail /path/to/thumb.png
```

パレット/グリッドも生成（既定でON）:
```bash
./scripts/with_ytm_env.sh python3 scripts/vision/vision_pack.py thumbnail /path/to/thumb.png --palette-k 10 --grid 4x3
```

---

## 2) 出力（何ができるか）

既定の出力先:
- `workspaces/tmp/vision_packs/<kind>_<timestamp>/`

主な生成物:
- `raw.png`: 向き補正 + PNG正規化
- `enh2x.png`: 拡大 + コントラスト/シャープ（小文字対策）
- `gray.png`: グレー化
- `edge.png`: エッジ（構図/境界の把握）
- `bin2x_*.png`: 二値化（`otsu` / `otsu_inv` / `fixed`）
- `crops/` + `crops.json` + `crops_overlay.png`: テキスト領域候補の自動切り出し（bbox付き）
- `palette.json`（thumbnailモード）: k-means代表色（hex + 比率）
- `grid/`（thumbnailモード）: 4x3などで分割したセル画像
- `ocr.json` + `ocr_all.txt`（`--ocr` 指定時）: OCR結果（best-effort）
- `pack.json`: 生成物の索引
- `images_for_llm.txt`: LLM投入向けの画像リスト（カンマ区切り）
- `prompt_template.txt`: 推測禁止・追加切り出し要求つきのプロンプト雛形

---

## 3) LLMへ渡すときの型（推奨）

1) `images_for_llm.txt` の順に画像を投入（raw → 強調 → 二値化 → crops）
2) `prompt_template.txt` をベースに指示（推測禁止 / [??] / 追加切り出し）
3) 読めない箇所が残る場合:
   - `crops_overlay.png` を見て、足りない箇所を追加で切り出す（手動crop）  
   - もしくは `--scale 3` や `--bin-threshold 50/70` 等で再パック

---

## 4) OCR（任意・best-effort）

`--ocr` は以下のいずれかで動作（あれば使う）:
- `pytesseract`（Python）
- `tesseract`（CLI）

注意:
- 日本語OCRは `jpn` の学習データが必要な場合がある（環境依存）。
- OCRが空/崩れる場合でも、`bin2x_otsu.png` / `bin2x_otsu_inv.png` と `crops/` をLLMへ渡すだけで改善することが多い。


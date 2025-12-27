# CH02 034+「ノイズ画像」修正 引き継ぎ書（SSOT）

- 最終更新: 2025-12-13
- 対象: CH02 の CapCut ドラフトで **画像が灰色ノイズ（プレースホルダ）**になっている件（ユーザー報告: 034〜）。
- 目的: 次のエージェントが **100%再現可能**に「実画像へ置換 → ドラフト再構築 → 検証 ✅」まで完走できるようにする。

---

## 0. 結論（何が起きていて、何をすれば直るか）

### 症状
- CapCutドラフト上で画像が「入っていない」ように見える（実際は **ノイズ画像が挿入**されている）。
- `tools/validate_ch02_drafts.py` の出力で以下が出る:
  - `images: looks like placeholder noise images (uniform large PNG sizes ~5.76-5.77MB)`

### 原因
- 画像生成をスキップする経路（placeholder/no-LLM）でドラフトを作った結果、`bootstrap_placeholder_run_dir.py` が作る **PILノイズPNG** が `assets/image/*.png` にコピーされてしまっている。

### 直し方（必須2工程）
1) **run_dir の images を実画像で再生成**（`image_cues.json` は既にある前提）
2) **CapCutドラフトを再ビルド**して `assets/image/*.png` を実画像に差し替える  
   （run_dir側の画像を差し替えるだけでは CapCut 側の参照が更新されないため、必ず再ビルドが必要）

---

## 1. 事前チェック（必須）

### 1.1 ロック（並列衝突防止）
作業前にロックを確認し、作業対象にロックを置く。

```bash
python scripts/agent_org.py locks --path packages/video_pipeline/tools/**
python scripts/agent_org.py lock --mode no_touch --ttl-min 120 --note "CH02 noise images fix" \
  packages/video_pipeline/tools/** \
  packages/factory_common/image_client.py \
  ssot/handoffs/CH02_IMAGES_NOISE_FIX/**
```

### 1.2 必要なキー/ネットワーク
- 画像生成は Gemini 画像API（`GEMINI_API_KEY`）を使用。
```bash
python - <<'PY'
import os
print("GEMINI_API_KEY", "OK" if os.getenv("GEMINI_API_KEY") else "MISSING")
PY
```

### 1.3 CapCut draft root
- 通常: `~/Movies/CapCut/User Data/Projects/com.lveditor.draft`
- もしこのパスに書き込みできない環境（EPERM/TCC）なら、`auto_capcut_run.py` がローカルrootへフォールバックする（その場合は **手動コピーが必要**）。

---

## 2. 影響範囲の確定（必須）

まず **現状のドラフトを機械検証**して、ノイズが入っている動画番号を確定する。

```bash
PYTHONPATH=".:packages" python3 -m video_pipeline.tools.validate_ch02_drafts \
  --channel CH02 \
  --videos 034,035,036,037,038,039,040,041
```

- `✅` 以外（ノイズ判定 / そもそもMissing）は “完成扱い禁止”

---

## 3. 修正手順（推奨: 既存 run_name を活かして置換）

### 3.1 対象 run_dir を選ぶ（超重要）
対象ドラフトが例えば:
- `~/Movies/CapCut/.../CH02-034_regen_20251213_091300_draft`

なら、対応する run_name は:
- `CH02-034_regen_20251213_091300`

run_dir は:
- `workspaces/video/runs/CH02-034_regen_20251213_091300/`

**条件**
- run_dir に `image_cues.json` が存在すること
- run_dir に `images/` ディレクトリがあること（空でも可。生成で埋める）

⚠️ `*_regen_img*` の一部は `image_cues.json` が無い不完全runがあるので、基本は使わない。

### 3.2 画像を実生成で置換（run_dir 側）
まずは 1枚だけで疎通確認（推奨）:

```bash
PYTHONPATH=".:packages" python3 -m video_pipeline.tools.regenerate_images_from_cues \
  --run workspaces/video/runs/CH02-034_regen_20251213_091300 \
  --channel CH02 \
  --force \
  --max 1
```

問題なければ全枚数:

```bash
PYTHONPATH=".:packages" python3 -m video_pipeline.tools.regenerate_images_from_cues \
  --run workspaces/video/runs/CH02-034_regen_20251213_091300 \
  --channel CH02 \
  --force
```

補足:
- 生成速度/429対策は `SRT2IMAGES_IMAGE_MAX_PER_MINUTE` で調整可能（デフォルト10）。
  - 例: `export SRT2IMAGES_IMAGE_MAX_PER_MINUTE=6`
- 生成に失敗して画像が欠けた場合は、同コマンドを再実行（繰り返しで埋まる運用）。

### 3.3 CapCutドラフトを再ビルド（ここで assets/image が置換される）
SoT の SRT は必ず final を使う:
- `workspaces/audio/final/CH02/034/CH02-034.srt`

```bash
PYTHONPATH=".:packages" python3 -m video_pipeline.tools.auto_capcut_run \
  --channel CH02 \
  --srt workspaces/audio/final/CH02/034/CH02-034.srt \
  --run-name CH02-034_regen_20251213_091300 \
  --resume \
  --nanobanana none \
  --belt-mode existing \
  --template CH02-テンプレ \
  --draft-name-policy run \
  --no-draft-name-with-title
```

ポイント:
- `--resume --nanobanana none` により **画像生成はしない**（run_dir/images をそのまま使う）
- その代わり、draft 側の `assets/image/*.png` は run_dir/images からコピーされるため、ここでノイズが実画像に置換される

### 3.4 メイン帯テキスト同期（念のため毎回）
```bash
PYTHONPATH=".:packages" python3 -m video_pipeline.tools.set_ch02_belt_from_status \
  --channel CH02 \
  --videos 034 \
  --update-run-belt-config
```

### 3.5 機械検証（必須）
```bash
PYTHONPATH=".:packages" python3 -m video_pipeline.tools.validate_ch02_drafts \
  --channel CH02 \
  --videos 034
```

期待:
- `✅ CH02-034_..._draft`

失敗時の典型原因:
- run_name の取り違え（別runを再ビルドしている）
- run_dir/images の生成が未完（欠番/0枚）
- CapCut draft root が別（ローカルrootへ書かれていて validate が見ていない）

---

## 4. 代替手段（新規 run_name で作り直す）
既存run_dirが壊れている場合のみ検討。

```bash
PYTHONPATH=".:packages" python3 -m video_pipeline.tools.build_ch02_drafts_range \
  --channel CH02 \
  --videos 034-080 \
  --mode images
```

⚠️ `--mode placeholder` はデバッグ専用（ノイズ画像を作る）なので使用禁止。

---

## 5. 作業記録（必須）
- 作業記録は `TEMPLATE_WORKLOG.md` をコピーして、その日のログとして残す。
- 1動画ごとに「run_name」「cue数」「画像生成結果」「validate結果」を必ず書く。

---

## 6. 現状（この引き継ぎ作成時点の観測）
- `validate_ch02_drafts.py --videos 034-040` で **ノイズ判定が出る**ことを確認。
- 例: `CH02-035_regen_20251213_092000` で `regenerate_images_from_cues.py --max 1` が成功し、実画像が生成できることを確認。

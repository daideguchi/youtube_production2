# OPS_CAPCUT_CH02_DRAFT_SOP (SSOT)

- 最終更新日: 2025-12-13  
- 目的: **CH02のCapCutドラフトを「CH02-テンプレ」から崩さず**に生成し、右上メイン帯のデザイン維持・音声挿入・字幕黒背景スタイルを **機械検証で100%担保**する。
- 適用範囲: `packages/video_pipeline/tools/*` によるCapCutドラフト生成（CH02）。

---

## 1. 重要な前提（SoT）

- **音声/SRT SoT**: `workspaces/audio/final/CH02/{NNN}/CH02-{NNN}.wav|.srt`
- **Video run SoT**: `workspaces/video/runs/{run_name}/`
  - `image_cues.json`, `images/`, `belt_config.json` が前提
- **CapCut draft root**: `$HOME/Movies/CapCut/User Data/Projects/com.lveditor.draft`
- **テンプレ**: `CH02-テンプレ`（これ以外を使わない）

---

## 2. 絶対要件（Fail-fast）

CH02ドラフトは次を必ず満たすこと（満たさない生成は“完成扱い禁止”）:

1. **右上メイン帯（青帯）デザインがテンプレのまま**
   - `draft_info.json` の `belt_main_track` セグメント0で
     - `extra_material_refs` がテンプレと一致
     - `clip.transform/scale` が存在
     - 参照する `materials.effects` が存在
2. **音声（voiceover）が必ず挿入されている**
   - `draft_content.json` の `voiceover` トラックに segment が存在
   - `materials/audio/*.wav` が存在
3. **字幕がCapCutデフォルト挿入の黒背景スタイル**
   - `subtitles_text` の text material が
     - `background_style=1`, `background_color=#000000`, `background_alpha=1.0`
     - `line_spacing=0.12`

上の要件は `tools/validate_ch02_drafts.py` で機械検証する（後述）。

---

## 3. 生成フロー（動画1本）

### 3.1 run_dirをfinalに整合（ズレ疑いがある場合は必須）

> cuesが古いSRT由来だと字幕/音声と映像がズレる。LLMなしで整合できる。

```bash
PYTHONPATH=".:packages" python3 -m video_pipeline.tools.align_run_dir_to_tts_final \
  --run workspaces/video/runs/{run_name}
```

### 3.2 CapCutドラフト生成（テンプレ土台・音声挿入あり）

```bash
PYTHONPATH=".:packages" python3 -m video_pipeline.tools.auto_capcut_run \
  --channel CH02 \
  --srt workspaces/audio/final/CH02/{NNN}/CH02-{NNN}.srt \
  --run-name {run_name} \
  --title "{belt_title}" \
  --resume \
  --belt-mode existing \
  --nanobanana none \
  --template "CH02-テンプレ"
```

- `--resume` で画像生成はスキップ（既存run_dirを使用）
- `--title` はLLMタイトル生成の回避（空にしない）

### 3.3 メイン帯テキストを正本に合わせる（必須）

SSOT（`status.json` の `metadata.sheet_title`【】）からメイン帯文字を確定させる。

```bash
PYTHONPATH=".:packages" python3 -m video_pipeline.tools.set_ch02_belt_from_status \
  --channel CH02 \
  --videos {NNN} \
  --update-run-belt-config
```

（テンプレのレイヤ/スタイルを壊さず、`belt_main_text` の文字列のみ差し替える）

---

## 4. 機械検証（必須）

生成後、必ずバリデータを通す（Fail-fast）。

```bash
PYTHONPATH=".:packages" python3 -m video_pipeline.tools.validate_ch02_drafts \
  --channel CH02 \
  --videos {NNN}
```

- `✅` 以外が出たら “完成扱い禁止”
- 対応:
  1) `align_run_dir_to_tts_final.py` を実行（cuesがズレている可能性）
  2) `auto_capcut_run.py --resume ...` を再実行（draftは置換される）
  3) 再度 `validate_ch02_drafts.py`

---

## 5. 対象一括（CH02-014/019-033）

```bash
PYTHONPATH=".:packages" python3 -m video_pipeline.tools.validate_ch02_drafts \
  --channel CH02 \
  --videos 014,019,020,021,022,023,024,025,026,027,028,029,030,031,032,033
```

全て `✅` になれば CapCut側で編集/書き出しに進める。

---

## 関連SSOT

- CapCutドラフトの内部仕様・安全運用・復旧手順（全チャンネル共通）: `ssot/ops/OPS_CAPCUT_DRAFT_SOP.md`
- CapCutドラフト作成/編集の運用（Macメイン + 共有ストレージ連携）: `ssot/ops/OPS_CAPCUT_DRAFT_EDITING_WORKFLOW.md`
- CapCutドラフト（編集）資産の置き場と共有運用: `ssot/ops/OPS_CAPCUT_DRAFT_STORAGE_STRATEGY.md`

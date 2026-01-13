# CapCut Draft SOP (All Channels)

## 0. Environment
- Load keys from one place: set `GEMINI_API_KEY` in the project `.env` (or export in your shell). No `.gemini_config` / credentials copies.
- nanobanana modes: `batch` (Gemini Batch when supported), `direct` (sync ImageClient), `none` (skip). Default is `batch` for video images.

## 1. Run command (no fallbacks)
```
PYTHONPATH=".:packages" python3 -m video_pipeline.tools.auto_capcut_run \
	  --channel <CHxx> \
	  --srt workspaces/video/input/<channel_dir>/<ep>.srt \
	  --run-name <run> \
	  --title "<title>" \
	  --belt-mode llm \
	  --nanobanana batch \
	  --img-concurrency 3 \
	  --force
```
- 帯は動的生成のみ（LLM 推奨 / grouped は `chapters.json` + `episode_info.json` が揃っている場合のみ）。等分/固定ラベルのフォールバックは禁止。
- 生成後に `belt_config.json` を目視（章名が LLM/chapters 由来か）。

## 2. Persona block (must-have per project)
- 1ブロックで「誰・年齢・性別・人種/地域・髪型/服装・舞台/時代」を明示。
- チャンネルプリセットの prompt/style/suffix を読み込み、案件固有の persona で上書き。
- 禁則を必ず付与: 「文字/看板/字幕を描かない」「アニメ誇張なし」「実写NGはプリセットに従う」。
- すべての生成/再生成で同一ブロックを再利用。

## 3. Draft build & title
- `auto_capcut_run` が `belt_config.json` を適用し、`inject_title_json` でタイトルを注入。
- CapCut draft: `~/Movies/CapCut/User Data/Projects/com.lveditor.draft/<ep>_draft`
- `auto_run_info.json` を確認: `belt_mode=grouped`, `template`, `title`, `replacements` をログ。
- CH01 の時間オフセット: `opening_offset=0.0s`（映像/帯/タイトルも 0 秒開始。黒画面挿入なし）。

## 4. Image regen + replace（唯一の方法）
1) `image_regenerator` で `custom_prompt=persona付き` で再生成（必要カットのみ）。  
2) `safe_image_swap.py` **のみ使用する**（他の差し替えツールは使用禁止）  
   - 例: `GEMINI_API_KEY=... PYTHONPATH=".:packages" python3 -m video_pipeline.tools.safe_image_swap --run-dir <run> --draft "<draft_path>" --indices <list> --style-mode illustration --custom-prompt "<persona>" --only-allow-draft-substring "手動調整後4" --skip-full-sync`  
   - draft_content の srt2images セグメントだけを差し替え、draft_info のトラック構造は触らない。  
   - 差し替え後、`PYTHONPATH=".:packages" python3 -m video_pipeline.tools.sync_srt2images_materials --draft "<draft_path>"` で srt2images トラックの material_id を draft_info に同期（timerange/トラック構造は不変）。  
3) CapCutで該当カットを目視確認。  
4) `auto_run_info.json` の `replacements` を確認。必要なら `docs/run_notes*.md` に番号を追記。

## 5. Belt/Title QA
- `belt_config.json` がLLM/chapters由来になっているか確認（等分ラベルが混入していないか）。
- タイトルが案件指定どおりか `draft_content.json` もしくは CapCut UI で確認。
- CH02（右上メイン帯）: テンプレ由来の帯デザイン（位置/青背景）が消えていたら、テンプレから復元する:
  - `PYTHONPATH=".:packages" python3 -m video_pipeline.tools.restore_template_belt_design --template 'CH02-テンプレ' --draft-regex '^CH02-..._draft$'`

## 6. Checklist (per run)
- [ ] GEMINI_API_KEY / config OK
- [ ] `--belt-mode llm` 実行（または grouped=chapters あり）、belt_config 目視済み
- [ ] persona ブロック定義し、全プロンプトに反映
- [ ] 再生成は persona 付きで実施、差し替えログ反映
- [ ] CapCut で帯＋代表カットを目視

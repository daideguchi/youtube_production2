# OPS_ALIGNMENT_CHECKPOINTS — SoT整合チェック（壊さないための確定チェックリスト）

目的:
- 「どれがゴミか」「どこが正本か」を誤判定しないために、フェーズごとの SoT と最低条件を **チェックリスト化** して確定する。

関連:
- 確定フロー: `ssot/OPS_CONFIRMED_PIPELINE_FLOW.md`
- I/Oスキーマ: `ssot/OPS_IO_SCHEMAS.md`
- ログ: `ssot/OPS_LOGGING_MAP.md`
- 生成物ライフサイクル: `ssot/PLAN_OPS_ARTIFACT_LIFECYCLE.md`

---

## A. Planning（企画/進捗CSV）

対象:
- `workspaces/planning/channels/CHxx.csv`（互換: `progress/channels/CHxx.csv`）

最低条件（最低限ここが揃わないと下流で事故る）:
- [ ] ファイル名 `CHxx.csv` と列 `チャンネル` の値が一致している（存在する場合）
- [ ] 各行で動画番号が取れる（`動画番号` または `No.` が空でない）
- [ ] 各行で動画IDが取れる（`動画ID` または `台本番号` が空でない）
- [ ] `CHxx-NNN` 形式が壊れていない（`NNN` は 3 桁推奨）

推奨（迷子/再生成ミスが激減する）:
- [ ] `台本` または `台本パス` が `workspaces/scripts/{CH}/{NNN}/content/assembled.md` を指す（互換: `script_pipeline/data/...`）
  - 形式は **repo 相対パス** を推奨（絶対パス混入は誤参照/移設事故の原因）
- [ ] 企画更新後に下流を再生成する運用が守られている（`OPS_PLANNING_CSV_WORKFLOW.md`）

---

## B. Script（台本 SoT）

対象:
- `workspaces/scripts/{CH}/{NNN}/status.json`（正本。互換: `script_pipeline/data/...`）
- `workspaces/scripts/{CH}/{NNN}/content/assembled.md`（正本。互換: `script_pipeline/data/...`）

最低条件:
- [ ] `status.json` が存在する
- [ ] `status.json` に `script_id`, `channel`, `metadata`, `status`, `stages` が存在する（許容スキーマは `OPS_IO_SCHEMAS.md`）
- [ ] `content/assembled.md` が存在し、空ではない

推奨:
- [ ] `metadata.title` と Planning CSV の `タイトル` が一致（差分がある場合は「どちらが正か」を決めて reset する）
- [ ] `updated_at` が更新されている（いつ作られたか追える）
- [ ] `metadata.alignment.schema == "ytm.alignment.v1"` が入っている（Planning↔Scriptの整合スタンプ）
  - 更新が入ったら再スタンプする: `python scripts/enforce_alignment.py --channels CHxx --apply`

---

## C. Audio/TTS（音声・字幕 SoT）

対象（下流参照の正本）:
- `workspaces/audio/final/{CH}/{NNN}/`（正本。互換: `audio_tts_v2/artifacts/final/...`）

最低条件:
- [ ] `{CH}-{NNN}.wav` が存在する
- [ ] `{CH}-{NNN}.srt` が存在する
- [ ] `log.json` が存在し、`channel`, `video`, `engine`, `segments` を含む（許容スキーマは `OPS_IO_SCHEMAS.md`）

推奨:
- [ ] `log.json` の `channel/video` とディレクトリ `{CH}/{NNN}` が一致する
- [ ] 中間生成物（`workspaces/scripts/.../audio_prep/`。互換: `script_pipeline/data/...`）は final 生成後に cleanup できる（規約: `PLAN_OPS_ARTIFACT_LIFECYCLE.md`）

---

## D. Video（SRT→画像→CapCut）

対象（run 単位の SoT）:
- `workspaces/video/runs/{run_id}/`（正本。互換: `commentary_02_srt2images_timeline/output/...`）

最低条件:
- [ ] `image_cues.json` が存在し、`cues[]` が空でない
- [ ] `images/` が存在し、`cues` と概ね対応する枚数がある
- [ ] `capcut_draft_info.json` が存在する（CapCut Draft 連携を使う場合）

推奨:
- [ ] `auto_run_info.json` があれば run の入力（CH/NNN/元SRT等）が追える
- [ ] `belt_config.json` があれば帯の仕様が追える（run により存在しない場合あり）

---

## E. Thumbnails（独立動線）

対象（SoT）:
- `thumbnails/projects.json`

最低条件:
- [ ] `projects.json` が JSON として読める
- [ ] 各案件に `channel`, `video`, `variants[]` がある

推奨:
- [ ] `variants[].image_path` が実ファイルへ辿れる（`thumbnails/assets/` or 旧資産ディレクトリ）
- [ ] 選定/承認の履歴を残す（`ssot/history/HISTORY_codex-memory.md`）

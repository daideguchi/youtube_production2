# OPS_SCRIPT_SOURCE_MAP — 台本/音声/動画の“ソース元”対応表（SoT→生成物）

この文書は「何をどこで直すべきか（正本/SoT）」を最短で判断するためのソースマップ。  
迷ったら **“直す場所＝SoT”** を先に確定し、派生物（ミラー/生成物）は後から同期する。

関連: `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`, `ssot/plans/PLAN_OPS_ARTIFACT_LIFECYCLE.md`

---

## 0. 大原則（壊さないためのルール）

- **SoTは1つ**：フェーズごとに“正本”は1つに限定する（複数あると必ず破綻する）。
- **ミラーは編集禁止**：UI/集計の都合で写しているだけ。編集するなら同期ツールとセット。
- **生成物は原則“再生成可能”**：SoT以外の中間物/ログは基本的に捨ててよい（必要なら再生成できる）。保持/削除は `PLAN_OPS_ARTIFACT_LIFECYCLE` を正本にする。
- **パスはpaths SSOT**：コードは `packages/factory_common/paths.py` を通す（物理移設しても壊れないため）。

---

## 1. Planning（企画）— “何を作るか”の正本

### 1.1 SoT
- `workspaces/planning/channels/CHxx.csv`（企画の正本）
  - 主要列（例）:
    - `チャンネル`, `動画番号`, `動画ID`, `タイトル/Topic`, `タグ/要約`, `status`, `redo_*` 等
  - 参照側:
    - `packages/script_pipeline/tools/planning_store.py`（都度CSVを読む）
    - `packages/video_pipeline/src/srt2images/llm_context_analyzer.py`（画像文脈へ注入）
    - `apps/ui-backend/backend/*`（UI表示/編集）

### 1.2 Mirror（編集禁止）
- UIのキャッシュ/サマリ系（例: `apps/ui-backend/backend` が生成する quick_history など）

### 1.3 下流へ流れる“ソース”一覧
- タイトル/テーマ → 台本プロンプト / CapCutタイトル / YouTube投稿
- タグ/要約/企画意図 → 画像生成のLLM文脈 / 台本の論旨
- 進捗ステータス（ready/published等）→ cleanupの安全ガード

---

## 2. Script Pipeline（台本）— “本文”の正本

### 2.1 SoT
- `workspaces/scripts/{CH}/{NNN}/status.json`（正本）
  - ステージ状態（pending/completed）と出力ファイルの存在が正本

### 2.2 Human-editable（人間が直すならここ）
- **Aテキスト（正本）**:
  - 優先: `workspaces/scripts/{CH}/{NNN}/content/assembled_human.md`
  - 代替: `workspaces/scripts/{CH}/{NNN}/content/assembled.md`
  - ルール:
    - `assembled_human.md` が存在する場合は **それが正本**（以降の音声生成もこれを優先）。
    - `assembled.md` は **ミラー/互換入力**。`assembled_human.md` がある状態での手動編集は禁止（混乱の元）。

### 2.3 Generated（派生物）
- `workspaces/scripts/{CH}/{NNN}/logs/*_prompt.txt`, `*_response.json`（証跡/デバッグ）
- `workspaces/scripts/{CH}/{NNN}/content/*`（段階生成物、運用で採用するファイルを固定する）

### 2.4 入口（Entry points）
- `python -m script_pipeline.cli init/run/next/run-all ...`
  - `--channel CHxx --video NNN` を正として扱う（パス直書き禁止）

---

## 3. Audio/TTS（音声・SRT）— “下流が読む音声”の正本

### 3.1 SoT（下流参照の正本）
- `workspaces/audio/final/{CH}/{NNN}/`
  - `{CH}-{NNN}.wav`（strict。旧運用では `.flac` 等もある）
  - `{CH}-{NNN}.srt`
  - `a_text.txt`（**実際に合成したTTS入力（=Bテキスト）のスナップショット**）
  - `log.json`（証跡。読み解決/分割/エンジンメタの根拠）
  - 旧運用で存在する場合あり: `b_text.txt` / `b_text_with_pauses.txt` / `engine_metadata.json` / `kana_engine.json` など（互換/監査用）

### 3.2 Intermediate（作業残骸：消して良い）
- `workspaces/scripts/{CH}/{NNN}/audio_prep/`
  - `chunks/`（最大容量。finalが揃ったら削除対象）
  - `log.json`（finalへ同期済みなら削除対象）
  - `pause_map.json`, `srt_blocks.json`, `tokens.json` 等（保持ポリシーは `PLAN_OPS_ARTIFACT_LIFECYCLE`）

### 3.3 入口（Entry points）
- `python -m script_pipeline.cli audio --channel CHxx --video NNN [--resume]`
- `PYTHONPATH=".:packages" python3 -m audio_tts.scripts.run_tts --channel CHxx --video NNN --input <assembled.md>`

---

## 4. Video（SRT→画像→CapCutドラフト）— “run_dir”が正本

### 4.1 SoT（run単位の正本）
- `workspaces/video/runs/{run_id}/`
  - `image_cues.json`
  - `capcut_draft/`（採用ドラフト）
  - `belt_config.json`, `auto_run_info.json`（再現/監査に必要）

**採用run（1:1の入口）**
- 1エピソードにrunが複数ある場合、採用runは `workspaces/scripts/{CH}/{NNN}/status.json` の `metadata.video_run_id` を正本にする。
- 補助（リンク集）: `workspaces/episodes/{CH}/{NNN}/`（`scripts/episode_ssot.py materialize` が生成）

### 4.2 Inputs（上流からのソース）
- SRT: `workspaces/audio/final/{CH}/{NNN}/{CH}-{NNN}.srt`
- 音声: `workspaces/audio/final/{CH}/{NNN}/{CH}-{NNN}.wav`
- 企画文脈: `workspaces/planning/channels/CHxx.csv`
- チャンネルpreset: `packages/video_pipeline/config/channel_presets.json`

### 4.3 入口（Entry points）
- `PYTHONPATH=".:packages" python3 -m video_pipeline.tools.auto_capcut_run ...`
- `PYTHONPATH=".:packages" python3 -m video_pipeline.tools.factory ...`（UI/ジョブからも呼ばれる）

---

## 5. Thumbnails（サムネ）— 独立動線の正本

- SoT: `workspaces/thumbnails/projects.json`
- 画像: `workspaces/thumbnails/assets/{CH}/{NNN}/...`
- ※サムネは音声/SRT→CapCutの主動線とは独立（ただし企画CSVを参照する場合がある）

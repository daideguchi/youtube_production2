# OPS_ENTRYPOINTS_INDEX — 実行入口（CLI/スクリプト/UI）の確定リスト

目的:
- 「何を叩けば何が走るか」を確定し、処理フローの誤解とゴミ判定ミスを防ぐ。
- リファクタリング時に **互換レイヤ（入口）から順に守る** ための索引にする。

正本フロー: `ssot/OPS_CONFIRMED_PIPELINE_FLOW.md`

---

## 1. 最重要（E2E主動線）

- 企画（Planning SoT）: `progress/channels/CHxx.csv`（UIでも編集）
- 台本（Script）: `python -m script_pipeline.cli ...`（`script_pipeline/cli.py`）
- 音声（Audio/TTS）:
  - 推奨: `python -m script_pipeline.cli audio --channel CHxx --video NNN`（wrapper）
  - 直叩き: `python audio_tts_v2/scripts/run_tts.py ...`
- 動画（SRT→画像→CapCut）:
  - `python commentary_02_srt2images_timeline/tools/auto_capcut_run.py ...`
  - `python commentary_02_srt2images_timeline/tools/factory.py ...`（UI/ジョブ運用からも呼ばれる）
- 投稿（YouTube）:
  - `python scripts/youtube_publisher/publish_from_sheet.py --max-rows 1 --run`

---

## 2. UI（運用の入口）

- FastAPI backend: `apps/ui-backend/backend/main.py`（互換: `ui/backend/main.py` は symlink）
  - 音声/SRTの参照は final を正本として扱う（`audio_tts_v2/artifacts/final/...`）
  - VideoProduction（CapCut系ジョブ）: `apps/ui-backend/backend/video_production.py`
    - `commentary_02_srt2images_timeline/ui/server/jobs` を呼び出す
- Frontend (React): `apps/ui-frontend`（互換: `ui/frontend` は symlink）

---

## 3. ドメイン別CLI（代表）

### 3.1 Script pipeline
- `script_pipeline/cli.py`
- `script_pipeline/job_runner.py`
- `script_pipeline/tools/channel_prompt_sync.py`

### 3.2 Audio/TTS
- `audio_tts_v2/scripts/run_tts.py`
- `audio_tts_v2/scripts/extract_reading_candidates.py`
- `audio_tts_v2/scripts/sync_voicevox_user_dict.py`

### 3.3 Video/CapCut（commentary_02）
- `commentary_02_srt2images_timeline/tools/auto_capcut_run.py`
- `commentary_02_srt2images_timeline/tools/run_pipeline.py`
- `commentary_02_srt2images_timeline/tools/srt_to_capcut_complete.py`（旧統合版・運用は要確認）
- `commentary_02_srt2images_timeline/tools/align_run_dir_to_tts_final.py`（run_dir の cue を final SRT に retime / LLMなし）
- `commentary_02_srt2images_timeline/tools/sync_*`（同期/保守）
- `commentary_02_srt2images_timeline/tools/maintenance/*`（修復系）

---

## 4. 生成物の掃除（容量/混乱対策）

- `scripts/sync_audio_prep_to_final.py`（prep→final不足同期）
- `scripts/purge_audio_prep_binaries.py`（prep重複wav/srt削除）
- `scripts/cleanup_audio_prep.py`（prep/chunks削除）
- 実行ログ: `ssot/OPS_CLEANUP_EXECUTION_LOG.md`

---

## 5. 自動抽出（argparse / __main__ 検出）

以下は「CLIっぽい入口」をコードから機械抽出した一覧（過不足あり）。  
分類（Active/Legacy/Archive）は `ssot/PLAN_LEGACY_AND_TRASH_CLASSIFICATION.md` の基準で確定させる。

- `audio_tts_v2/scripts/extract_reading_candidates.py`
- `audio_tts_v2/scripts/run_contextual_reading_llm.py`
- `audio_tts_v2/scripts/run_tts.py`
- `audio_tts_v2/scripts/sync_voicevox_user_dict.py`
- `commentary_02_srt2images_timeline/tools/auto_capcut_run.py`
- `commentary_02_srt2images_timeline/tools/capcut_bulk_insert.py`
- `commentary_02_srt2images_timeline/tools/factory.py`
- `commentary_02_srt2images_timeline/tools/run_pipeline.py`
- `commentary_02_srt2images_timeline/tools/srt_to_capcut_complete.py`
- `commentary_02_srt2images_timeline/tools/align_run_dir_to_tts_final.py`
- `script_pipeline/cli.py`
- `script_pipeline/job_runner.py`
- `scripts/youtube_publisher/publish_from_sheet.py`
- `ui/backend/main.py`

再抽出コマンド例:
- `rg -l "argparse\\.ArgumentParser|if __name__ == '__main__'" <dirs...> | sort`

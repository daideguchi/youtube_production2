# OPS_ENTRYPOINTS_INDEX — 実行入口（CLI/スクリプト/UI）の確定リスト

目的:
- 「何を叩けば何が走るか」を確定し、処理フローの誤解とゴミ判定ミスを防ぐ。
- リファクタリング時に **互換レイヤ（入口）から順に守る** ための索引にする。

正本フロー: `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`
工程別の「使う/使わない（禁止）」: `ssot/ops/OPS_SCRIPTS_PHASE_CLASSIFICATION.md`

---

## 1. 最重要（E2E主動線）

- 企画（Planning SoT）: `workspaces/planning/channels/CHxx.csv`（互換: `progress/channels/CHxx.csv`）
- 台本（Script / 入口固定）: `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/script_runbook.py <mode> ...`
  - 運用モード正本（new/redo-full/resume/rewrite）: `ssot/ops/OPS_SCRIPT_FACTORY_MODES.md`
  - 低レベルCLI（内部/詳細制御）: `./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli ...`（`script_pipeline/cli.py`）
- 音声（Audio/TTS）:
  - 推奨: `python -m script_pipeline.cli audio --channel CHxx --video NNN`（wrapper）
  - 直叩き: `PYTHONPATH=".:packages" python3 -m audio_tts_v2.scripts.run_tts ...`
- 動画（SRT→画像→CapCut）:
  - `PYTHONPATH=".:packages" python3 -m commentary_02_srt2images_timeline.tools.auto_capcut_run ...`
  - `PYTHONPATH=".:packages" python3 -m commentary_02_srt2images_timeline.tools.factory ...`（UI/ジョブ運用からも呼ばれる）
- 投稿（YouTube）:
  - `python scripts/youtube_publisher/publish_from_sheet.py --max-rows 1 --run`

---

## 2. UI（運用の入口）

- 起動（推奨）: `bash scripts/start_all.sh start`
  - 内部で `apps/ui-backend/tools/start_manager.py` を呼び出し、必要な同期/ヘルスチェックも実施する。
- ヘルスチェック（ガード込み）: `python3 apps/ui-backend/tools/start_manager.py healthcheck --with-guards`
- FastAPI backend: `apps/ui-backend/backend/main.py`（互換: `ui/backend/main.py` は symlink）
  - 音声/SRTの参照は final を正本として扱う（`workspaces/audio/final/...`。互換: `audio_tts_v2/artifacts/final/...`）
  - VideoProduction（CapCut系ジョブ）: `apps/ui-backend/backend/video_production.py`
    - `commentary_02_srt2images_timeline/server/jobs.py` を呼び出す
  - チャンネル登録（scaffold）:
    - `POST /api/channels/register`（handle→channel_id 解決 + channels/planning/persona/sources.yaml 雛形生成）
  - Script pipeline 運用補助（pipeline-boxes）
    - `GET /api/channels/{ch}/videos/{video}/script-manifest`（ステージ一覧/出力）
    - `GET|PUT /api/channels/{ch}/videos/{video}/llm-artifacts/*`（THINK MODEでの手動補正→出力反映）
    - `POST /api/channels/{ch}/videos/{video}/script-pipeline/reconcile`（既存出力から status.json を補正）
    - `POST /api/channels/{ch}/videos/{video}/script-pipeline/run/script_validation`（Aテキスト品質ゲートを再実行）
  - BatchTTS（UIパネル）:
    - `POST /api/batch-tts/start`（backend が `scripts/batch_regenerate_tts.py` を起動）
    - `GET /api/batch-tts/progress`, `GET /api/batch-tts/log`, `POST /api/batch-tts/reset`
- Frontend (React): `apps/ui-frontend`（互換: `ui/frontend` は symlink）

---

## 3. ドメイン別CLI（代表）

### 3.1 Script pipeline
- `script_pipeline/cli.py`
- `script_pipeline/job_runner.py`
- `script_pipeline/tools/channel_prompt_sync.py`
- `script_pipeline/tools/channel_registry.py`（新チャンネル追加: handle→channel_id 解決 + sources.yaml/CSV/Persona 雛形生成）
- ベンチマーク/タグ/説明文の一括整備（channel_info 正規化 + カタログ再生成）:
  - `python3 scripts/ops/channel_info_normalize.py`（dry-run）
  - `python3 scripts/ops/channel_info_normalize.py --apply`
- `scripts/buddha_senior_5ch_prepare.py`（CH12–CH16: status init + metadata補完）
- `scripts/buddha_senior_5ch_generate_scripts.py`（CH12–CH16: 台本一括生成（APIなし））
- Planning lint（決定論・混入検知）:
  - `python3 scripts/ops/planning_lint.py --csv workspaces/planning/channels/CHxx.csv --write-latest`
- Script運用Runbook（新規/やり直しの定型化）:
  - モード正本: `ssot/ops/OPS_SCRIPT_FACTORY_MODES.md`
  - `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/script_runbook.py new --channel CH10 --video 004`
  - `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/script_runbook.py redo-full --channel CH07 --from 019 --to 030`
  - `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/script_runbook.py resume --channel CH07 --video 019`
  - `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/script_runbook.py rewrite --channel CH07 --video 019 --instruction \"言い回しをもっと理解しやすい表現に\"`
  - 既存本文を通すだけ（安い）: `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/script_runbook.py redo --channel CH07 --from 019 --to 030 --mode validate`
- Planning sanitize（決定論・L3混入クリーナ。dry-runがデフォルト）:
  - `python3 scripts/ops/planning_sanitize.py --channel CHxx --write-latest`（dry-run）→ 必要時のみ `--apply`
- Aテキスト lint（決定論・反復/禁則混入検知）:
  - `python3 scripts/ops/a_text_lint.py --channel CHxx --video NNN --write-latest`
- 長尺Aテキスト（セクション分割→合成）:
  - `python3 scripts/ops/a_text_section_compose.py --channel CHxx --video NNN`（dry-run）
  - `python3 scripts/ops/a_text_section_compose.py --channel CHxx --video NNN --apply --run-validation`
  - 設計: `ssot/ops/OPS_SCRIPT_GENERATION_ARCHITECTURE.md`
- 超長尺Aテキスト（Marathon: 2〜3時間級 / 全文LLM禁止）:
  - `python3 scripts/ops/a_text_marathon_compose.py --channel CHxx --video NNN --duration-minutes 120 --plan-only`
  - `python3 scripts/ops/a_text_marathon_compose.py --channel CHxx --video NNN --duration-minutes 120`（dry-run: `content/analysis/longform/` に出力）
  - `python3 scripts/ops/a_text_marathon_compose.py --channel CHxx --video NNN --duration-minutes 120 --apply`（canonical を上書き）
  - Memory投入を切る（debug/特殊ケース）:
    - `python3 scripts/ops/a_text_marathon_compose.py --channel CHxx --video NNN --duration-minutes 120 --no-memory`
  - ブロック雛形（章の箱）を指定したい場合:
    - `python3 scripts/ops/a_text_marathon_compose.py --channel CHxx --video NNN --duration-minutes 120 --block-template personal_benefit_v1 --apply`
    - 正本: `configs/longform_block_templates.json`
  - SSOT: `ssot/ops/OPS_LONGFORM_SCRIPT_SCALING.md`
- Aテキスト補助（既存台本の修復・短尺補正）:
  - sanitize（脚注/URLなどのメタ混入除去）:
    - `python3 scripts/sanitize_a_text.py --channel CHxx --videos NNN --mode run`
  - expand（短すぎる台本の増補。長尺はMarathon推奨）:
    - `python3 scripts/expand_a_text.py --channel CHxx --video NNN --mode run --hint "水増し禁止/現代の作り話禁止"`

### 3.2 Audio/TTS
- `audio_tts_v2/scripts/run_tts.py`
- `audio_tts_v2/scripts/extract_reading_candidates.py`
- `audio_tts_v2/scripts/sync_voicevox_user_dict.py`

### 3.3 Video/CapCut（commentary_02）
- `commentary_02_srt2images_timeline/tools/auto_capcut_run.py`
- `commentary_02_srt2images_timeline/tools/run_pipeline.py`
- `commentary_02_srt2images_timeline/tools/srt_to_capcut_complete.py`（旧統合版・運用は要確認）
- `commentary_02_srt2images_timeline/tools/bootstrap_placeholder_run_dir.py`（run_dir を cues+images でブートストラップ。THINK MODE では `visual_image_cues_plan` が pending 化）
- `commentary_02_srt2images_timeline/tools/build_ch02_drafts_range.py`（CH02の一括ドラフト生成ラッパー）
- `commentary_02_srt2images_timeline/tools/align_run_dir_to_tts_final.py`（run_dir の cue を final SRT に retime / LLMなし）
- `commentary_02_srt2images_timeline/tools/patch_draft_audio_subtitles_from_manifest.py`（テンプレdraftに audio/subtitles を SoT(manifest) から注入）
- `commentary_02_srt2images_timeline/tools/validate_ch02_drafts.py`（CH02 draft 破壊検知: belt/voice/subtitles）
- `commentary_02_srt2images_timeline/tools/sync_*`（同期/保守）
- `commentary_02_srt2images_timeline/tools/maintenance/*`（修復系）

### 3.4 Agent/THINK MODE（複数AIエージェント運用）
- `scripts/think.sh`（THINK MODE 一発ラッパー）
- `scripts/agent_runner.py`（pending/results キュー操作、外部チャット用 prompt 生成）
- `scripts/agent_org.py`（Orchestrator/Agents/Locks/Memos の協調運用。`overview` で「誰が何を触っているか」俯瞰可能）
	  - Shared Board（単一ファイルで共同）:
	    - status: `python scripts/agent_org.py board set ...`
	    - notes: `python scripts/agent_org.py board note ...`（返信: `--reply-to <note_id>`）
	    - show: `python scripts/agent_org.py board show`
	    - note全文: `python scripts/agent_org.py board note-show <note_id>`
	    - threads: `python scripts/agent_org.py board threads` / `python scripts/agent_org.py board thread-show <thread_id|note_id>`
	    - ownership: `python scripts/agent_org.py board areas` / `python scripts/agent_org.py board area-set <AREA> ...`
	    - 記法テンプレ（BEP-1）: `python scripts/agent_org.py board template`
	    - legacy補正（note_id無しが混ざった場合）: `python scripts/agent_org.py board normalize`
	  - UI:
	    - `/agent-board`（Shared Board: ownership/threads/notes）
	    - `/agent-org`（Agents/Locks/Memos の統合表示）
	  - API:
	    - `GET /api/agent-org/overview`（Agents+Locks+Memos を統合表示）
	    - `GET /api/agent-org/board`（Shared Board JSON）
	    - `POST /api/agent-org/board/status`（status更新）
	    - `POST /api/agent-org/board/note`（note投稿/返信）
	    - `POST /api/agent-org/board/area`（ownership更新）

### 3.5 Episode（A→B→音声→SRT→run の1:1整備）
- `scripts/episode_ssot.py`（video_run_id の自動選択/episodeリンク集の生成）

### 3.6 Alignment（Planning↔Script 整合スタンプ）
- `scripts/enforce_alignment.py`（dry-runがデフォルト。`--apply` で `workspaces/scripts/{CH}/{NNN}/status.json: metadata.alignment` を更新）
  - UIの進捗一覧は `整合/整合理由` を表示し、「どれが完成版？」の混乱を早期に検出する。
- `scripts/audit_alignment_semantic.py`（read-only。タイトル/サムネcatch ↔ 台本文脈の語彙整合を監査。`--out` でJSON保存可）
- `./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli semantic-align --channel CHxx --video NNN`（意味整合: タイトル/サムネ訴求 ↔ 台本コア を定性的にチェック/修正）
  - 運用SoT: `ssot/ops/OPS_SEMANTIC_ALIGNMENT.md`

### 3.7 Remotion（実験ライン / 再レンダ）
- 直接レンダ（1本）: `node apps/remotion/scripts/render.js --help`
- バッチレンダ（容量節約・lock尊重・report出力）: `python3 scripts/ops/render_remotion_batch.py --help`

---

## 4. 生成物の掃除（容量/混乱対策）

- 統合 cleanup（推奨）:
  - audio: `python -m scripts.cleanup_workspace --dry-run --channel CHxx --video NNN` → OKなら `--run`
  - video runs: `python -m scripts.cleanup_workspace --video-runs --dry-run --channel CHxx --video NNN` → OKなら `--run`
  - video runs（unscoped/legacyも整理）: `python -m scripts.cleanup_workspace --video-runs --all --dry-run --video-unscoped-only --video-archive-unscoped --video-archive-unscoped-legacy --keep-recent-minutes 1440` → OKなら `--run --yes`
  - broken symlinks: `python -m scripts.cleanup_workspace --broken-symlinks --dry-run` → OKなら `--run`（必要なら `--symlinks-include-episodes`）
  - logs: `python -m scripts.cleanup_workspace --logs --dry-run` → OKなら `--run`
  - scripts: `python -m scripts.cleanup_workspace --scripts --dry-run` → OKなら `--run`
- 復旧（run dir を戻す）:
  - `python scripts/ops/restore_video_runs.py --report workspaces/video/_archive/<timestamp>/archive_report.json` → OKなら `--run`
- `scripts/sync_audio_prep_to_final.py`（prep→final不足同期）
- `scripts/purge_audio_prep_binaries.py`（prep重複wav/srt削除）
- `scripts/cleanup_audio_prep.py`（prep/chunks削除）
- `scripts/purge_audio_final_chunks.py`（final/chunks削除）
- `scripts/cleanup_data.py --run`（workspaces/scripts の古い中間生成物/ログを削除。`audio_prep/` は final 音声が揃っている動画のみ対象）
- `scripts/ops/cleanup_logs.py --run`（logs 直下の L3 ログを日数ローテで削除。report: `logs/regression/logs_cleanup/`）
- `scripts/ops/logs_snapshot.py`（logs の現状スナップショット: 件数/サイズ）
- `scripts/ops/cleanup_caches.sh`（`__pycache__` / `.pytest_cache` / `.DS_Store` 削除）
- `scripts/ops/cleanup_broken_symlinks.py --run`（壊れた `capcut_draft` symlink を削除して探索ノイズを減らす。report: `logs/regression/broken_symlinks/`）
- `scripts/ops/cleanup_remotion_artifacts.py --run`（Remotion 生成物 `apps/remotion/out` と `apps/remotion/public/_bgm/_auto` を keep-days でローテ。report: `logs/regression/remotion_cleanup/`）
- `scripts/ops/prune_video_run_legacy_files.py --run`（`workspaces/video/runs/**` の `*.legacy.*` を archive-first で prune。report: `logs/regression/video_runs_legacy_prune/`）
- `scripts/ops/archive_capcut_local_drafts.py --run`（`workspaces/video/_capcut_drafts` のローカル退避ドラフトを `_archive/<timestamp>/` へ移動して探索ノイズ/重複を削減。report: `logs/regression/capcut_local_drafts_archive/`）
- `scripts/ops/archive_thumbnails_legacy_channel_dirs.py --run`（`workspaces/thumbnails/CHxx_*|CHxx-*` の旧ディレクトリを `_archive/<timestamp>/` へ退避して探索ノイズを削減。report: `logs/regression/thumbnails_legacy_archive/`）
- `scripts/ops/purge_legacy_agent_task_queues.py --run`（旧 `logs/agent_tasks_*`（実験残骸）を archive-first で削除。report: `logs/regression/agent_tasks_legacy_purge/`）
- `python -m commentary_02_srt2images_timeline.tools.sync_audio_inputs --wav-policy symlink --wav-dedupe`（`workspaces/video/input` の wav を symlink 化して重複を減らす。必要なら `--hash-wav`）
- 実行ログ: `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`

## 4.1 SSOTメンテ（索引/計画書の整合）

- `python3 scripts/ops/ssot_audit.py`（SSOT索引/PLAN_STATUS の整合チェック）
  - 監査ログを残す: `python3 scripts/ops/ssot_audit.py --write`
  - completed も厳密に索引化する: `python3 scripts/ops/ssot_audit.py --strict`
- `python3 scripts/ops/scripts_inventory.py --write`（`scripts/**` 棚卸しSSOTを再生成: `ssot/ops/OPS_SCRIPTS_INVENTORY.md`）

---

## 5. 自動抽出（argparse / __main__ 検出）

以下は「CLIっぽい入口」をコードから機械抽出した一覧（過不足あり）。  
分類（Active/Legacy/Archive）は `ssot/plans/PLAN_LEGACY_AND_TRASH_CLASSIFICATION.md` の基準で確定させる。

- `audio_tts_v2/scripts/extract_reading_candidates.py`
- `audio_tts_v2/scripts/run_contextual_reading_llm.py`
- `audio_tts_v2/scripts/run_tts.py`
- `audio_tts_v2/scripts/sync_voicevox_user_dict.py`
- `commentary_02_srt2images_timeline/tools/auto_capcut_run.py`
- `commentary_02_srt2images_timeline/tools/capcut_bulk_insert.py`
- `commentary_02_srt2images_timeline/tools/bootstrap_placeholder_run_dir.py`
- `commentary_02_srt2images_timeline/tools/factory.py`
- `commentary_02_srt2images_timeline/tools/build_ch02_drafts_range.py`
- `commentary_02_srt2images_timeline/tools/run_pipeline.py`
- `commentary_02_srt2images_timeline/tools/srt_to_capcut_complete.py`
- `commentary_02_srt2images_timeline/tools/align_run_dir_to_tts_final.py`
- `commentary_02_srt2images_timeline/tools/patch_draft_audio_subtitles_from_manifest.py`
- `commentary_02_srt2images_timeline/tools/validate_ch02_drafts.py`
- `script_pipeline/cli.py`
- `script_pipeline/job_runner.py`
- `scripts/youtube_publisher/publish_from_sheet.py`
- `ui/backend/main.py`

再抽出コマンド例:
- `rg -l "argparse\\.ArgumentParser|if __name__ == '__main__'" <dirs...> | sort`

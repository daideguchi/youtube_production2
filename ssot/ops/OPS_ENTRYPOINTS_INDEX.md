# OPS_ENTRYPOINTS_INDEX — 実行入口（CLI/スクリプト/UI）の確定リスト

目的:
- 「何を叩けば何が走るか」を確定し、処理フローの誤解とゴミ判定ミスを防ぐ。
- リファクタリング時に **互換レイヤ（入口）から順に守る** ための索引にする。

正本フロー: `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`
工程別の「使う/使わない（禁止）」: `ssot/ops/OPS_SCRIPTS_PHASE_CLASSIFICATION.md`

---

## 0. 統一入口（迷わないための推奨）

- 一覧: `./ops list`
- 事前点検: `./ops doctor`
- 迷子/復帰:
  - 進捗ビュー（read-only）: `./ops progress --channel CHxx --format summary`
  - “最新の実行” ポインタ（keep-latest）: `./ops latest --channel CHxx --video NNN`
  - 実行タイムライン（opsレジャー）: `./ops history --tail 50 --channel CHxx --video NNN`
  - 復帰（固定）: `./ops resume <episode|script|audio|video|thumbnails> ...`（正本: `ssot/ops/OPS_FIXED_RECOVERY_COMMANDS.md`）
- Reconcile（fixed recoveryの配線）:
  - `./ops reconcile --channel CHxx --video NNN`（dry-run）
  - `./ops reconcile --channel CHxx --video NNN --llm think --run`
- SSOT（最新ロジック確認）:
  - `./ops ssot status`
  - `./ops ssot audit --strict`
- 処理パターン（CLIレシピSSOT）:
  - `./ops patterns list`
  - `./ops patterns show PAT-VIDEO-DRAFT-001`
  - 正本: `ssot/ops/OPS_EXECUTION_PATTERNS.md`
- 代表例（P0ラッパー）:
  - Script: `./ops script <MODE> --channel CHxx --video NNN`
  - Audio: `./ops audio --channel CHxx --video NNN`
  - Publish: `./ops publish ...`
- LLM実行の明示（重要）:
  - `--llm think`: **外部LLM APIコストを使わない**（agent queue に pending を作る → `./ops agent ...` で埋める）
  - `--llm api`: 外部LLM API（通常）
  - `--llm codex`: `codex exec`（**明示した時だけ**）
- 迷わない短縮（強制; `--llm` の付け忘れ防止）:
  - `./ops think <cmd> ...`（常に THINK MODE）
  - `./ops api <cmd> ...`（常に API）
  - `./ops codex <cmd> ...`（常に codex exec。明示した時だけ）
  - `YTM_OPS_TIPS=0` で `./ops` のヒント表示（stderr）を無効化できる（default: ON）
- 復帰コマンド固定（SSOT）: `ssot/ops/OPS_FIXED_RECOVERY_COMMANDS.md`

## 1. 最重要（E2E主動線）

- 企画（Planning SoT）: `workspaces/planning/channels/CHxx.csv`
- 企画カード在庫（pre-planning SoT）: `workspaces/planning/ideas/CHxx.jsonl`
  - CLI: `python3 scripts/ops/idea.py --help`
  - 運用SSOT: `ssot/ops/OPS_IDEA_CARDS.md`
- 台本（Script / 入口固定）: `./scripts/with_ytm_env.sh python3 scripts/ops/script_runbook.py <MODE> ...`
  - 運用モード正本（new/redo-full/resume/rewrite/seed-expand）: `ssot/ops/OPS_SCRIPT_FACTORY_MODES.md`
  - カオス復旧（複数エージェント競合の止血）: `ssot/ops/OPS_SCRIPT_INCIDENT_RUNBOOK.md`
  - 低レベルCLI（内部/詳細制御。通常運用では使わない）: `./scripts/with_ytm_env.sh python3 -m script_pipeline.cli ...`（`packages/script_pipeline/cli.py`）
- 音声（Audio/TTS）:
  - 推奨: `python -m script_pipeline.cli audio --channel CHxx --video NNN`（wrapper）
  - 直叩き: `PYTHONPATH=".:packages" python3 -m audio_tts.scripts.run_tts ...`
- 動画（SRT→画像→CapCut）:
  - `PYTHONPATH=".:packages" python3 -m video_pipeline.tools.auto_capcut_run ...`
    - 任意（飽き防止B-roll）:
      - デフォルト: OFF（`configs/sources.yaml: channels.CHxx.video_broll.enabled=false`）
      - ON時の既定: provider=`pexels` / ratio=`0.2`（= 画像:フリー素材 8:2）
      - CLI上書き: `--broll-provider {none|pixel|pexels|pixabay|coverr} --broll-ratio 0.2`（要env: `PEXELS_API_KEY` / `PIXABAY_API_KEY` / `COVERR_API_KEY`）
      - 容量対策（推奨デフォルト）:
        - mp4は共有キャッシュ + hardlink 再利用（重複DL/重複保存を抑制）: `YTM_BROLL_FILE_CACHE=1`
        - 解像度上限（既定=720p）: `YTM_BROLL_MAX_W=1280`, `YTM_BROLL_MAX_H=720`
    - CH02（既定mix）: gemini:schnell:フリー動画 = `4:3:3`
      - SoT: `configs/sources.yaml: channels.CH02.image_source_mix`
      - 適用（dry-run推奨）:
        - `PYTHONPATH=".:packages" python3 -m video_pipeline.tools.apply_image_source_mix <run_dir> --weights 4:3:3 --gemini-model-key g-1 --schnell-model-key f-1 --broll-provider pexels --dry-run`
        - `PYTHONPATH=".:packages" python3 -m video_pipeline.tools.apply_image_source_mix <run_dir> --weights 4:3:3 --gemini-model-key g-1 --schnell-model-key f-1 --broll-provider pexels`
      - 画像を埋めた/直した後のドラフト再構築（推奨）:
        - `./ops resume video --llm think --channel CHxx --video NNN`
  - `PYTHONPATH=".:packages" python3 -m video_pipeline.tools.factory ...`（UI/ジョブ運用からも呼ばれる）
- 投稿（YouTube）:
  - 最小（uploadのみ）: `python scripts/youtube_publisher/publish_from_sheet.py --max-rows 1 --run`
  - 推奨（事故防止: ローカルも投稿済みロック同期）: `python scripts/youtube_publisher/publish_from_sheet.py --max-rows 1 --run --also-lock-local`
    - 任意（一時DL先を固定）: `--download-dir workspaces/tmp/publish` / 成功後も残す: `--keep-download`

---

## 2. UI（運用の入口）

- 起動（推奨）: `bash scripts/start_all.sh start`
  - 内部で `apps/ui-backend/tools/start_manager.py` を呼び出し、必要な同期/ヘルスチェックも実施する。
- ヘルスチェック（ガード込み）: `python3 apps/ui-backend/tools/start_manager.py healthcheck --with-guards`
- FastAPI backend: `apps/ui-backend/backend/main.py`
  - 音声/SRTの参照は final を正本として扱う（`workspaces/audio/final/...`）
  - VideoProduction（CapCut系ジョブ）: `apps/ui-backend/backend/video_production.py`
    - `packages/video_pipeline/server/jobs.py` を呼び出す
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
- Frontend (React): `apps/ui-frontend`
  - 配線SSOT（UI↔Backend）: `ssot/ops/OPS_UI_WIRING.md`
  - API base URL（GitHub Pages / 別origin向け）: `apps/ui-frontend/src/api/baseUrl.ts`（`REACT_APP_API_BASE_URL`）
- Script Viewer（GitHub Pages / 静的）: `docs/`
  - 索引生成（台本一覧・パス）: `python3 scripts/ops/pages_script_viewer_index.py --write`
  - Deploy: `.github/workflows/pages_script_viewer.yml`（GitHub Actions → Pages。公開ブランチは `main`）

---

## 3. ドメイン別CLI（代表）

### 3.1 Script pipeline
- `packages/script_pipeline/cli.py`
- `packages/script_pipeline/job_runner.py`
- `packages/script_pipeline/tools/channel_prompt_sync.py`
- `packages/script_pipeline/tools/channel_registry.py`（新チャンネル追加: handle→channel_id 解決 + sources.yaml/CSV/Persona 雛形生成）
- 完成台本ファクトチェック（単発）:
  - `./scripts/with_ytm_env.sh python3 scripts/ops/fact_check_codex.py --channel CHxx --video NNN`
- ベンチマーク/タグ/説明文の一括整備（channel_info 正規化 + カタログ再生成）:
  - `python3 scripts/ops/channel_info_normalize.py`（dry-run）
  - `python3 scripts/ops/channel_info_normalize.py --apply`
- Research索引（workspaces/research をジャンルで逆引きできるINDEXを生成）:
  - `python3 scripts/ops/research_genre_index.py`（dry-run）
  - `python3 scripts/ops/research_genre_index.py --apply`
- `scripts/buddha_senior_5ch_prepare.py`（CH12–CH16: status init + metadata補完）
- `scripts/buddha_senior_5ch_generate_scripts.py`（CH12–CH16: 台本一括生成（APIなし））
- Planning lint（機械チェック・混入検知）:
  - `python3 scripts/ops/planning_lint.py --csv workspaces/planning/channels/CHxx.csv --write-latest`
- Production Pack（量産投入前のスナップショット + QA gate）:
  - `python3 scripts/ops/production_pack.py --channel CHxx --video NNN --write-latest`
  - SSOT: `ssot/ops/OPS_PRODUCTION_PACK.md`
- Pre-production audit（入口〜投入前の抜け漏れ監査）:
  - `python3 scripts/ops/preproduction_audit.py --all --write-latest`
  - SSOT: `ssot/ops/OPS_PREPRODUCTION_FRAME.md`
- Planning Patch（企画の上書き/部分更新を差分ログ付きで適用）:
  - `python3 scripts/ops/planning_apply_patch.py --patch workspaces/planning/patches/<PATCH>.yaml --apply`
  - まとめ変更（patch雛形の一括生成）: `python3 scripts/ops/planning_patch_gen.py --help`
  - SSOT: `ssot/ops/OPS_PLANNING_PATCHES.md`
- SRT（字幕本文の意図改行を付与。内容は変えない）:
  - `python3 scripts/format_srt_linebreaks.py workspaces/audio/final/CHxx/NNN/CHxx-NNN.srt --in-place`
  - SSOT: `ssot/ops/OPS_SRT_LINEBREAK_FORMAT.md`
- CH01（人生の道標）台本執筆の補助:
  - 企画CSV→入力テンプレ生成: `python3 scripts/ch01/generate_prompt_input.py --video-id CH01-216`
  - Aテキスト簡易セルフチェック: `python3 scripts/ch01/check_script.py workspaces/scripts/CH01/216/content/assembled_human.md`
- Script運用Runbook（新規/やり直しの定型化）:
  - モード正本: `ssot/ops/OPS_SCRIPT_FACTORY_MODES.md`
  - `./scripts/with_ytm_env.sh python3 scripts/ops/script_runbook.py new --channel CH10 --video 008`
  - `./scripts/with_ytm_env.sh python3 scripts/ops/script_runbook.py seed-expand --channel CH10 --video 008`
  - `./scripts/with_ytm_env.sh python3 scripts/ops/script_runbook.py redo-full --channel CH07 --from 019 --to 030`
  - `./scripts/with_ytm_env.sh python3 scripts/ops/script_runbook.py resume --channel CH07 --video 019`
  - `./scripts/with_ytm_env.sh python3 scripts/ops/script_runbook.py rewrite --channel CH07 --video 019 --instruction \"言い回しをもっと理解しやすい表現に\"`
  - 既存本文を通すだけ（安い）: `./scripts/with_ytm_env.sh python3 scripts/ops/script_runbook.py redo --channel CH07 --from 019 --to 030 --mode validate`
  - 途中から再開（バッチ/範囲・resetしない）: `./scripts/with_ytm_env.sh python3 scripts/ops/script_runbook.py redo --channel CH07 --from 019 --to 030 --mode continue`
- Planning sanitize（機械チェック・混入クリーナ。dry-runがデフォルト）:
  - `python3 scripts/ops/planning_sanitize.py --channel CHxx --write-latest`（dry-run）→ 必要時のみ `--apply`
- Planning 列の再整列（機械チェック・タイトルを正としてテーマ補助列のみ修正）:
  - `python3 scripts/ops/planning_realign_to_title.py --channel CHxx --from NNN --to MMM`（dry-run）
  - `python3 scripts/ops/planning_realign_to_title.py --channel CHxx --from NNN --to MMM --apply --write-latest`
- Aテキスト lint（機械チェック・反復/禁則混入検知）:
  - `python3 scripts/ops/a_text_lint.py --channel CHxx --video NNN --write-latest`
- 台本監査（対話AI・LLM API禁止 / 企画整合+流れを目視で確定）:
  - SSOT: `ssot/ops/OPS_DIALOG_AI_SCRIPT_AUDIT.md`
  - 対象スキャン: `python3 scripts/ops/dialog_ai_script_audit.py scan`
  - 判定反映（1本）: `python3 scripts/ops/dialog_ai_script_audit.py mark --channel CHxx --video NNN --verdict fail --reasons planning_misalignment,flow_break --note "導入が別テーマ/締めが不自然"`
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
- `packages/audio_tts/scripts/run_tts.py`
- `packages/audio_tts/scripts/extract_reading_candidates.py`
- `packages/audio_tts/scripts/sync_voicevox_user_dict.py`

### 3.3 Video/CapCut（video_pipeline）
- `packages/video_pipeline/tools/auto_capcut_run.py`
- `packages/video_pipeline/tools/run_pipeline.py`
  - 注意: `run_pipeline --engine capcut` は stub draft 生成（README.txt/draft_*.json）で、実運用の CapCut ドラフトではない。CapCut 主線は `auto_capcut_run.py` / `capcut_bulk_insert.py`。
- `packages/video_pipeline/tools/bootstrap_placeholder_run_dir.py`（run_dir を cues+images でブートストラップ。THINK MODE では `visual_image_cues_plan` が pending 化）
- `packages/video_pipeline/tools/validate_prompt_template_registry.py`（`channel_presets.json:prompt_template` が `template_registry.json` に登録されているか検査）
- `packages/video_pipeline/tools/build_ch02_drafts_range.py`（CH02の一括ドラフト生成ラッパー）
- `packages/video_pipeline/tools/align_run_dir_to_tts_final.py`（run_dir の cue を final SRT に retime / LLMなし）
- `packages/video_pipeline/tools/patch_draft_audio_subtitles_from_manifest.py`（テンプレdraftに audio/subtitles を SoT(manifest) から注入）
- `packages/video_pipeline/tools/validate_ch02_drafts.py`（CH02 draft 破壊検知: belt/voice/subtitles）
- `packages/video_pipeline/tools/regenerate_images_from_cues.py`（既存 run_dir の `image_cues.json` から `images/*.png` を実生成で再作成して置換）
- `packages/video_pipeline/tools/generate_image_variants.py`（既存 run_dir の `image_cues.json` から画像バリアントを生成。UI の Quick Job からも実行）
- `packages/video_pipeline/tools/sync_*`（同期/保守）

### 3.4 Agent/THINK MODE（複数AIエージェント運用）
- `scripts/think.sh`（THINK MODE 一発ラッパー）
- `scripts/agent_runner.py`（pending/results キュー操作、外部チャット用 prompt 生成）
- `scripts/agent_org.py`（Orchestrator/Agents/Locks/Memos の協調運用。`overview` で「誰が何を触っているか」俯瞰可能）
  - 重要: `lock/memo/board/*` などの **write系は agent name が必須**（`LLM_AGENT_NAME` または `--agent-name`。未設定なら初回プロンプトで入力→記憶される）
  - 並列運用の起動（Orchestrator一括ブート + preflight）: `python3 scripts/ops/orchestrator_bootstrap.py --name dd-orch`
  - preflight（ガードレール点検のみ）: `python3 scripts/ops/parallel_ops_preflight.py`
  - agent bootstrap（heartbeat + board更新）: `python3 scripts/ops/agent_bootstrap.py --name <NAME> --role <ROLE> --doing "..." --next "..."`
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

### 3.5 Thumbnails（サムネ量産/修正）
- SSOT: `ssot/ops/OPS_THUMBNAILS_PIPELINE.md`
- UI（モデル指定）: `/image-model-routing`（チャンネル別に サムネ/動画内画像 の画像モデルを指定）
- 統一CLI（量産/リテイク/QC）: `python scripts/thumbnails/build.py --help`
  - 量産: `python scripts/thumbnails/build.py build --channel CHxx --videos 001 002 ...`
  - リテイク: `python scripts/thumbnails/build.py retake --channel CHxx`（`projects.json: status=in_progress` を対象）
  - QC: `python scripts/thumbnails/build.py qc --channel CHxx --status in_progress`
- 競合サムネの特徴抽出→テンプレ雛形: `python3 scripts/ops/thumbnail_styleguide.py --help`（詳細: `ssot/ops/OPS_THUMBNAILS_PIPELINE.md`）

### 3.6 Vision（スクショ/サムネ読み取り補助）
- SSOT: `ssot/ops/OPS_VISION_PACK.md`
- CLI: `./scripts/with_ytm_env.sh python3 scripts/vision/vision_pack.py --help`
  - スクショ: `./scripts/with_ytm_env.sh python3 scripts/vision/vision_pack.py screenshot /path/to/screenshot.png`
  - サムネ: `./scripts/with_ytm_env.sh python3 scripts/vision/vision_pack.py thumbnail /path/to/thumb.png`

### 3.7 Episode（A→B→音声→SRT→run の1:1整備）
- `scripts/episode_ssot.py`（video_run_id の自動選択/episodeリンク集の生成）
- 進捗の統一ビュー（派生 / read-only）:
  - SSOT: `ssot/ops/OPS_EPISODE_PROGRESS_VIEW.md`
  - CLI: `python3 scripts/ops/episode_progress.py --channel CHxx`
  - API: `GET /api/channels/{ch}/episode-progress`（UI `/planning` が参照）

### 3.8 Alignment（Planning↔Script 整合スタンプ）
- `scripts/enforce_alignment.py`（dry-runがデフォルト。`--apply` で `workspaces/scripts/{CH}/{NNN}/status.json: metadata.alignment` を更新）
  - UIの進捗一覧は `整合/整合理由` を表示し、「どれが完成版？」の混乱を早期に検出する。
  - 注意: これは **整合スタンプ専用**。`redo_script` / `redo_note`（要対応の編集判断）は対話AI監査の管轄で、ここでは変更しない。
- `scripts/audit_alignment_semantic.py`（read-only。タイトル/サムネcatch ↔ 台本文脈の語彙整合を監査。`--out` でJSON保存可）
- `./scripts/with_ytm_env.sh python3 -m script_pipeline.cli semantic-align --channel CHxx --video NNN`（意味整合: タイトル/サムネ訴求 ↔ 台本コア を定性的にチェック/修正）
  - 運用SoT: `ssot/ops/OPS_SEMANTIC_ALIGNMENT.md`

### 3.9 Remotion（実験ライン / 再レンダ）
- UI（推奨 / 3100起動）: `/video-remotion` → 「Studio (3100) 起動」ボタン（`POST /api/remotion/restart_preview`）
  - deps未導入なら: `(cd apps/remotion && npm ci)`
- 直接レンダ（1本）: `node apps/remotion/scripts/render.js --help`
- バッチレンダ（容量節約・lock尊重・report出力）: `python3 scripts/ops/render_remotion_batch.py --help`

---

## 4. 生成物の掃除（容量/混乱対策）

- Gitロールバック事故の予防（push前チェック含む）:
  - `python3 scripts/ops/git_write_lock.py {status|lock|unlock|unlock-for-push}`（通常は lock。詳細: `ssot/ops/OPS_GIT_SAFETY.md`）
  - `python3 scripts/ops/pre_push_final_check.py --run-tests --write-ssot-report`（push前の最終チェック）
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
- `scripts/ops/cleanup_logs.py --run`（workspaces/logs 直下の L3 ログを日数ローテで削除。report: `workspaces/logs/regression/logs_cleanup/`）
- `scripts/ops/logs_snapshot.py`（logs の現状スナップショット: 件数/サイズ）
- `scripts/ops/cleanup_caches.sh`（`__pycache__` / `.pytest_cache` / `.DS_Store` 削除）
- `scripts/ops/cleanup_broken_symlinks.py --run`（壊れた `capcut_draft` symlink を削除して探索ノイズを減らす。report: `workspaces/logs/regression/broken_symlinks/`）
- `scripts/ops/cleanup_remotion_artifacts.py --run`（Remotion 生成物 `apps/remotion/out` と `apps/remotion/public/_bgm/_auto` を keep-days でローテ。report: `workspaces/logs/regression/remotion_cleanup/`）
- `scripts/ops/prune_video_run_legacy_files.py --run`（`workspaces/video/runs/**` の `*.legacy.*` を archive-first で prune。report: `workspaces/logs/regression/video_runs_legacy_prune/`）
- `scripts/ops/archive_published_episodes.py --dry-run --channel CHxx`（Planningの `進捗=投稿済み` を根拠に、audio/thumbnails/video input/runs を横断で `_archive/` へ移動。`--run --yes` で実行。report: `workspaces/logs/regression/archive_published_episodes/`）
- `scripts/ops/archive_capcut_local_drafts.py --run`（`workspaces/video/_capcut_drafts` のローカル退避ドラフトを `_archive/<timestamp>/` へ移動して探索ノイズ/重複を削減。report: `workspaces/logs/regression/capcut_local_drafts_archive/`）
- `scripts/ops/archive_thumbnails_legacy_channel_dirs.py --run`（`workspaces/thumbnails/CHxx_*|CHxx-*` の旧ディレクトリを `_archive/<timestamp>/` へ退避して探索ノイズを削減。report: `workspaces/logs/regression/thumbnails_legacy_archive/`）
- `scripts/ops/purge_legacy_agent_task_queues.py --run`（旧 `workspaces/logs/agent_tasks_*`（実験残骸）を archive-first で削除。report: `workspaces/logs/regression/agent_tasks_legacy_purge/`）
- `python -m video_pipeline.tools.sync_audio_inputs --wav-policy symlink --wav-dedupe`（`workspaces/video/input` の wav を symlink 化して重複を減らす。必要なら `--hash-wav`）
- 実行ログ: `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`

## 4.1 SSOTメンテ（索引/計画書の整合）

- `python3 scripts/ops/ssot_audit.py`（SSOT索引/PLAN_STATUS の整合チェック）
  - 監査ログを残す: `python3 scripts/ops/ssot_audit.py --write`
  - completed も厳密に索引化する: `python3 scripts/ops/ssot_audit.py --strict`
- `python3 scripts/ops/scripts_inventory.py --write`（`scripts/**` 棚卸しSSOTを再生成: `ssot/ops/OPS_SCRIPTS_INVENTORY.md`）
- `python3 scripts/ops/repo_sanity_audit.py --verbose`（tracked symlink / ルート互換symlink の再混入を検出）
- `python3 scripts/ops/prompts_inventory.py --write`（プロンプト索引 `prompts/PROMPTS_INDEX.md` を再生成）
- `python3 scripts/ops/repo_ref_audit.py --target <path-or-glob> --stdout`（参照ゼロの機械棚卸し）
- `python3 scripts/ops/docs_inventory.py --write`（非SSOT docs の参照棚卸し: `workspaces/logs/regression/docs_inventory/`）

---

## 5. 自動抽出（argparse / __main__ 検出）

以下は「CLIっぽい入口」をコードから機械抽出した一覧（過不足あり）。  
分類（Active/Legacy/Archive）は `ssot/plans/PLAN_LEGACY_AND_TRASH_CLASSIFICATION.md` の基準で確定させる。

- `packages/audio_tts/scripts/extract_reading_candidates.py`
- `packages/audio_tts/scripts/run_contextual_reading_llm.py`
- `packages/audio_tts/scripts/run_tts.py`
- `packages/audio_tts/scripts/sync_voicevox_user_dict.py`
- `packages/video_pipeline/tools/auto_capcut_run.py`
- `packages/video_pipeline/tools/capcut_bulk_insert.py`
- `packages/video_pipeline/tools/bootstrap_placeholder_run_dir.py`
- `packages/video_pipeline/tools/factory.py`
- `packages/video_pipeline/tools/build_ch02_drafts_range.py`
- `packages/video_pipeline/tools/run_pipeline.py`
- `packages/video_pipeline/tools/align_run_dir_to_tts_final.py`
- `packages/video_pipeline/tools/patch_draft_audio_subtitles_from_manifest.py`
- `packages/video_pipeline/tools/validate_ch02_drafts.py`
- `packages/video_pipeline/tools/generate_image_variants.py`
- `packages/script_pipeline/cli.py`
- `packages/script_pipeline/job_runner.py`
- `scripts/youtube_publisher/publish_from_sheet.py`
- `apps/ui-backend/backend/main.py`

再抽出コマンド例:
- `rg -l "argparse\\.ArgumentParser|if __name__ == '__main__'" <dirs...> | sort`

# HISTORY_codex-memory — 変更履歴（運用ログ）

目的:
- 「いつ / 何を / なぜ」変えたかを SSOT として残し、運用やリファクタリングの判断を誤らないようにする。

運用ルール:
- 1 エントリ = 1 セッション（または 1 日）
- 変更対象（ファイル/機能）と理由、影響範囲を短く書く
- 実行ログ（build/test/run の出力）は `logs/regression/*` 等へ保存し、本履歴からリンクする

過去ログ:
- 旧履歴は `_old/ssot_old/history/HISTORY_codex-memory.md` に残っている（参照専用）。

---

## 2025-12-12
- SSOT の参照パスを `ssot/` 直下へ正規化し、確定フロー/確定 I/O/ログマップの正本を更新（`ssot/OPS_CONFIRMED_PIPELINE_FLOW.md`, `ssot/OPS_IO_SCHEMAS.md`, `ssot/OPS_LOGGING_MAP.md`）。
- 大規模リファクタ前提の計画書を更新（`ssot/PLAN_REPO_DIRECTORY_REFACTOR.md`, `ssot/PLAN_LEGACY_AND_TRASH_CLASSIFICATION.md`, `ssot/PLAN_OPS_ARTIFACT_LIFECYCLE.md`）。
- 確実ゴミの削除を実施し、復元可能な形で記録（`ssot/OPS_CLEANUP_EXECUTION_LOG.md`）。

## 2025-12-13
- Target 構成への“無破壊”前進として `packages/`/`workspaces/`/`legacy/` の scaffold と互換symlinkを整備（`packages/README.md`, `workspaces/README.md`, `factory_common/paths.py`）。
- Stage3 legacy隔離を実施し、トップレベルを現行フロー中心に整理（`legacy/*` へ移動 + 互換symlink。実行記録は `ssot/OPS_CLEANUP_EXECUTION_LOG.md`）。
- UI の storage アクセスを non-DOM ビルドでも落ちないように安全化（`apps/ui-frontend/src/utils/safeStorage.ts`, `apps/ui-frontend/src/utils/workspaceSelection.ts`）。
- コア共通層のログ/設定/queue を `repo_root()`/`logs_root()` 経由に統一し、`packages/`/`workspaces/` 実体化に備えた（`factory_common/*`）。
- エントリポイントの一部を repo-root 安全化（`script_pipeline/validator.py` の DATA_ROOT を `script_data_root()` へ、`audio_tts_v2/scripts/run_tts.py` の `.env` 検出を pyproject 探索へ、`scripts/think.sh` のデフォルトを `workspaces/logs/agent_tasks` へ）。
- `./start.sh` の起動前チェックで Azure キー未設定でもブロックしないように変更（`scripts/check_env.py` を Azure 任意に、注意喚起は WARN のみに変更。併せて `configs/README.md`, `ssot/OPS_ENV_VARS.md` を更新）。
- THINK/AGENT 互換: `audio_tts_v2/scripts/run_contextual_reading_llm.py` を single-task 化（THINK/AGENT時はchunkせず1回で投げ、stop/resumeループを減らす）。
- THINK MODE 強化: srt2images の cues 計画を single-task 化（`visual_image_cues_plan`）し、機械分割ブートストラップを廃止。Visual Bible は per-run にスコープし cross-channel 混入を防止（`commentary_02_srt2images_timeline/src/srt2images/orchestration/pipeline.py`, `commentary_02_srt2images_timeline/src/srt2images/cues_plan.py`, `commentary_02_srt2images_timeline/tools/bootstrap_placeholder_run_dir.py`, `commentary_02_srt2images_timeline/src/srt2images/visual_bible.py`, `commentary_02_srt2images_timeline/src/srt2images/llm_context_analyzer.py`）。
- CapCut運用の安定化: タイトルは Planning CSV を優先し、テンプレ由来の汎用プレースホルダー（video_2/text_2等）を自動除去、字幕は最終段で黒背景スタイルへ正規化（`commentary_02_srt2images_timeline/tools/auto_capcut_run.py`, `commentary_02_srt2images_timeline/tools/capcut_bulk_insert.py`）。
- ゴミ削除: キャッシュ（`__pycache__`, `.pytest_cache`, `.DS_Store`）を除去し、旧PoC/旧静的ビルド（`legacy/50_tools`, `legacy/docs_old`）をアーカイブ後に削除（詳細: `ssot/OPS_CLEANUP_EXECUTION_LOG.md`）。
- マルチエージェント運用の下地: `AGENTS.md` と `ssot/OPS_AGENT_PLAYBOOK.md` を追加し、lock/SoT/削除/パッチ運用を明文化。運用コマンドを `scripts/ops/*` に集約。
- 設計/進捗の下地を強化（`ssot/PLAN_REPO_DIRECTORY_REFACTOR.md` に進捗追記、`README.md` のディレクトリ概要更新、`tests/test_paths.py` を新レイアウトに追従）。
- Stage2 前倒し（軽量領域）: planning/research を `workspaces/` 側へ実体化（`workspaces/planning`, `workspaces/research` が正本。旧 `progress`, `00_research` は symlink）。関連SSOTも新パスを正本として更新（`ssot/OPS_CONFIRMED_PIPELINE_FLOW.md`, `ssot/OPS_PLANNING_CSV_WORKFLOW.md`）。
- Stage2 cutover（audio/video）: `audio_tts_v2/artifacts` と `commentary_02_srt2images_timeline/{input,output}` を `workspaces/` 側へ実体化し、旧パスは symlink 化（実行: `python scripts/ops/stage2_cutover_workspaces.py --run`）。`workspaces/.gitignore` と `commentary_02_srt2images_timeline/.gitignore` を更新。
- ログ整理の導線を追加: `scripts/ops/cleanup_logs.py`（L3 logs ローテ）, `scripts/cleanup_data.py` を dry-run 既定 + keep-days ガードに更新し、古い script_pipeline logs を削除（記録: `ssot/OPS_CLEANUP_EXECUTION_LOG.md`）。
- UI backend のログDB参照を `logs_root()` に統一（`apps/ui-backend/backend/main.py`）。
- 検証: `python3 -m pytest -q tests/test_paths.py tests/test_llm_router.py tests/test_llm_client.py commentary_02_srt2images_timeline/tests/test_orchestration.py`
- Git備考: この環境では `.git` への新規書込みが拒否されるため、差分はパッチとして `backups/patches/*_stage2_cues_plan_paths.patch`, `backups/patches/*_stage3_capcut_tools.patch` に保存。

## 2025-12-14
- A/B/音声/SRT/run の“正本”を迷わないため、Aテキスト（assembled_human優先）とAudio final（a_text/b_text含む）をSSOTへ明記（`ssot/OPS_SCRIPT_SOURCE_MAP.md`, `workspaces/scripts/README.md`）。
- エピソード単位の1:1管理ツールを追加（`scripts/episode_ssot.py`）。`metadata.video_run_id` の自動/手動設定と `workspaces/episodes/{CH}/{NNN}/`（symlink + manifest）生成を提供。
- 生成物ライフサイクルの run 採用SoTを `status.json.metadata.video_run_id` に統一（`ssot/PLAN_OPS_ARTIFACT_LIFECYCLE.md`）。
- `workspaces/episodes/` をgitignoreしつつ README は保持（`workspaces/.gitignore`, `workspaces/README.md`, `workspaces/episodes/README.md`）。

## 2025-12-15
- CH06のCapCutドラフト混乱（音声/字幕不一致・完成版不明）を解消するため、run_dir を final SRT に再整合し、ドラフトへ音声WAV/字幕を manifest 正本から再注入（`commentary_02_srt2images_timeline/tools/align_run_dir_to_tts_final.py`, `commentary_02_srt2images_timeline/tools/patch_draft_audio_subtitles_from_manifest.py`）。
- 壊れた `capcut_draft` symlink を修復し、欠損していた CH06-031/032/033 のドラフトを再生成。Planning CSV の CH06-031 タイトル不整合も修正し、命名/参照のブレを抑制。CH06-テンプレの汚染（srt2images/subtitles/voiceover残骸）を除去し、再生成を安定化。
- CH06 の採用 run を `status.json.metadata.video_run_id` に固定し、未採用 run を `workspaces/video/_archive/` へ追加退避（詳細は `ssot/OPS_CLEANUP_EXECUTION_LOG.md`）。
- `workspaces/video/input` の同期を「既存でも不一致なら退避→更新」に修正し、stale SRT/WAV を一括で正本（audio/final）へ揃えた（`commentary_02_srt2images_timeline/tools/sync_audio_inputs.py`, 記録: `ssot/OPS_CLEANUP_EXECUTION_LOG.md`）。
- 既存CapCutドラフトの画像差し替え（再生成）で run_dir 推定が誤る問題を修正し、draft/run_dir 不一致は明示エラーにした（`apps/ui-backend/backend/routers/swap.py`）。併せて、既存ドラフトの validator が落ち続けないように foreign tracks をデフォルトWARN化し、必要時のみ strict で落とせるようにした（`packages/commentary_02_srt2images_timeline/tools/validate_srt2images_state.py`）。
- UIフリーズ対策: SwapImagesPage の画像を「手動ロード + 表示件数制限/カット指定 + lazy + サムネ」に変更し、backend 側も `/api/swap/images/file?max_dim=` でサムネを返せるようにした（`apps/ui-frontend/src/pages/SwapImagesPage.tsx`, `apps/ui-backend/backend/routers/swap.py`）。AudioReviewPage の `<audio preload>` を `none` にして一覧表示で大量 `/audio` Range が走らないようにした（`apps/ui-frontend/src/components/AudioReviewPage.tsx`）。
- 検証: `python3 -m py_compile apps/ui-backend/backend/routers/swap.py` / `npm -C apps/ui-frontend run build`

## 2025-12-16
- 全チャンネル共通の読み台本（Aテキスト）品質ルールをSSOT化（`ssot/OPS_A_TEXT_GLOBAL_RULES.md`）。`---` のみをポーズ挿入として許可し、`「」/（）` 多用・冗長/反復・URL/脚注混入などの事故要因を固定で禁止。
- 台本運用SSOTを更新し、Aテキストのグローバルルール参照と区切り記号の扱いを明文化（`ssot/OPS_SCRIPT_GUIDE.md`, `ssot/【消さないで！人間用】確定ロジック`）。
- チャンネルの固定コンテキストを1つに寄せるため、`configs/sources.yaml` を拡張し、planning/persona に加えて `channel_prompt` / `chapter_count` / 文字数目安を登録（全CHの参照点を統一）。
- script_pipeline の sources 読み込みを `configs/sources.yaml` 優先に切替え、既存 status.json にも欠損メタ（style/persona/prompt/表示名等）を安全に補完できるようにした（`packages/script_pipeline/runner.py`, `packages/script_pipeline/config/sources.yaml`）。
- 章生成プロンプトをTTS前提の自然さに寄せ、強制的な問いかけ/比喩のノルマを撤廃し、グローバルAテキストルールを注入（`packages/script_pipeline/prompts/chapter_prompt.txt`）。
- TTSのポーズマーカーを `---` のみに限定（`packages/audio_tts_v2/tts/strict_segmenter.py`）。
- Aテキストの品質チェック/拡張のための運用スクリプトを追加（`scripts/lint_a_text.py`, `scripts/expand_a_text.py`）。

## 2025-12-17
- `audio_sync_status.json` を code階層（packages）から排除し、状態ファイルとして `workspaces/video/_state/` に移設（`factory_common.paths.video_audio_sync_status_path()` + `commentary_02_srt2images_timeline/tools/sync_audio_inputs.py`）。差分ノイズと誤参照を削減。
- CapCut運用ツールを整理/強化（`commentary_02_srt2images_timeline/tools/*`）。画像スケール適用の点検ツールを追加（`capcut_apply_image_scale.py`）。
- Planning SoT を更新（`workspaces/planning/channels/CH02.csv`, `CH05.csv`, `CH06.csv`, `CH07.csv`）。
- 投稿済みロックを追加: `進捗=投稿済み` を最終固定とし、UI（Progress詳細）から `投稿済みにする（ロック）` をワンクリック実行できるようにした。内部APIは `POST /api/channels/{CH}/videos/{NNN}/published`（`factory_common/publish_lock.py`, `apps/ui-backend/backend/main.py`, `apps/ui-frontend/src/pages/ProgressPage.tsx`）。
- Cleanup（Remotion）: `apps/remotion/` 配下の未使用サンプルrun資産（画像/JSON）を archive-first 後に削除（記録: `ssot/OPS_CLEANUP_EXECUTION_LOG.md`）。
- Planning CSV の絶対パスを repo 相対へ正規化（CH01/02/03/05-11。CH04はロック中で保留）。`scripts/sync_all_scripts.py` / `scripts/sync_ch02_scripts.py` も今後は相対パスを書き出すよう更新。
- ドキュメント/運用例の `/Users/dd/...` を除去し、`<REPO_ROOT>` へ置換（`README.md`, `scripts/cleanup_data.md`, `scripts/youtube_publisher/README.md`, `ssot/OPS_TTS_MANUAL_READING_AUDIT.md`, `packages/script_pipeline/openrouter_tests_report.md` など）。
- `.gitignore` を整理: JSON を一律 ignore しない方針へ修正し、Remotionの生成物/ローカルキャッシュ（`apps/remotion/{input,out}` 等）と `data/visual_bible*.json` は個別に ignore。
- 検証: `npm -C apps/ui-frontend run build` / `python3 -m py_compile apps/ui-backend/backend/main.py` / `python3 -m py_compile scripts/sync_all_scripts.py scripts/sync_ch02_scripts.py`
- レガシー削除: `scripts/maintain_consciousness.py` を archive-first で削除し、`ssot/OPS_CLEANUP_EXECUTION_LOG.md` に記録（Step 39）。
- Planning テンプレも正規化: `workspaces/planning/templates/CH07..CH10_planning_template.csv` の台本パスを `workspaces/scripts/...`（repo相対）へ修正。

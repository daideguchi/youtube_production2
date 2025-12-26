# HISTORY_codex-memory — 変更履歴（運用ログ）

目的:
- 「いつ / 何を / なぜ」変えたかを SSOT として残し、運用やリファクタリングの判断を誤らないようにする。

運用ルール:
- 1 エントリ = 1 セッション（または 1 日）
- 変更対象（ファイル/機能）と理由、影響範囲を短く書く
- 実行ログ（build/test/run の出力）は `workspaces/logs/regression/*` 等へ保存し、本履歴からリンクする

過去ログ:
- 旧履歴は `_old/ssot_old/history/HISTORY_codex-memory.md` に残っている（参照専用）。

---

## 2025-12-12
- SSOT の参照パスを `ssot/` 直下へ正規化し、確定フロー/確定 I/O/ログマップの正本を更新（`ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`, `ssot/ops/OPS_IO_SCHEMAS.md`, `ssot/ops/OPS_LOGGING_MAP.md`）。
- 大規模リファクタ前提の計画書を更新（`ssot/plans/PLAN_REPO_DIRECTORY_REFACTOR.md`, `ssot/plans/PLAN_LEGACY_AND_TRASH_CLASSIFICATION.md`, `ssot/plans/PLAN_OPS_ARTIFACT_LIFECYCLE.md`）。
- 確実ゴミの削除を実施し、復元可能な形で記録（`ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`）。

## 2025-12-13
- Target 構成への“無破壊”前進として `packages/`/`workspaces/`/`legacy/` の scaffold と互換symlinkを整備（`workspaces/README.md`, `packages/factory_common/paths.py`）。
- Stage3 legacy隔離を実施し、トップレベルを現行フロー中心に整理（`legacy/*` へ移動 + 互換symlink。実行記録は `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`）。
- UI の storage アクセスを non-DOM ビルドでも落ちないように安全化（`apps/ui-frontend/src/utils/safeStorage.ts`, `apps/ui-frontend/src/utils/workspaceSelection.ts`）。
- コア共通層のログ/設定/queue を `repo_root()`/`logs_root()` 経由に統一し、`packages/`/`workspaces/` 実体化に備えた（`packages/factory_common/*`）。
- エントリポイントの一部を repo-root 安全化（`packages/script_pipeline/validator.py` の DATA_ROOT を `script_data_root()` へ、`packages/audio_tts_v2/scripts/run_tts.py` の `.env` 検出を pyproject 探索へ、`scripts/think.sh` のデフォルトを `workspaces/logs/agent_tasks` へ）。
- `./start.sh` の起動前チェックで Azure キー未設定でもブロックしないように変更（`scripts/check_env.py` を Azure 任意に、注意喚起は WARN のみに変更。併せて `configs/README.md`, `ssot/ops/OPS_ENV_VARS.md` を更新）。
- THINK/AGENT 互換: `packages/audio_tts_v2/scripts/run_contextual_reading_llm.py` を single-task 化（THINK/AGENT時はchunkせず1回で投げ、stop/resumeループを減らす）。
- THINK MODE 強化: srt2images の cues 計画を single-task 化（`visual_image_cues_plan`）し、機械分割ブートストラップを廃止。Visual Bible は per-run にスコープし cross-channel 混入を防止（`packages/commentary_02_srt2images_timeline/src/srt2images/orchestration/pipeline.py`, `packages/commentary_02_srt2images_timeline/src/srt2images/cues_plan.py`, `packages/commentary_02_srt2images_timeline/tools/bootstrap_placeholder_run_dir.py`, `packages/commentary_02_srt2images_timeline/src/srt2images/visual_bible.py`, `packages/commentary_02_srt2images_timeline/src/srt2images/llm_context_analyzer.py`）。
- CapCut運用の安定化: タイトルは Planning CSV を優先し、テンプレ由来の汎用プレースホルダー（video_2/text_2等）を自動除去、字幕は最終段で黒背景スタイルへ正規化（`packages/commentary_02_srt2images_timeline/tools/auto_capcut_run.py`, `packages/commentary_02_srt2images_timeline/tools/capcut_bulk_insert.py`）。
- ゴミ削除: キャッシュ（`__pycache__`, `.pytest_cache`, `.DS_Store`）を除去し、旧PoC/旧静的ビルド（`legacy/50_tools`, `legacy/docs_old`）をアーカイブ後に削除（詳細: `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`）。
- マルチエージェント運用の下地: `AGENTS.md` と `ssot/ops/OPS_AGENT_PLAYBOOK.md` を追加し、lock/SoT/削除/パッチ運用を明文化。運用コマンドを `scripts/ops/*` に集約。
- 設計/進捗の下地を強化（`ssot/plans/PLAN_REPO_DIRECTORY_REFACTOR.md` に進捗追記、`README.md` のディレクトリ概要更新、`tests/test_paths.py` を新レイアウトに追従）。
- Stage2 前倒し（軽量領域）: planning/research を `workspaces/` 側へ実体化（`workspaces/planning`, `workspaces/research` が正本。旧 `progress`, `00_research` は symlink）。関連SSOTも新パスを正本として更新（`ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`, `ssot/ops/OPS_PLANNING_CSV_WORKFLOW.md`）。
- Stage2 cutover（audio/video）: `packages/audio_tts_v2/artifacts` と `packages/commentary_02_srt2images_timeline/{input,output}` を `workspaces/` 側へ実体化し、旧パスは symlink 化（実行: `python scripts/ops/stage2_cutover_workspaces.py --run`）。`workspaces/.gitignore` と `packages/commentary_02_srt2images_timeline/.gitignore` を更新。
- ログ整理の導線を追加: `scripts/ops/cleanup_logs.py`（L3 logs ローテ）, `scripts/cleanup_data.py` を dry-run 既定 + keep-days ガードに更新し、古い script_pipeline logs を削除（記録: `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`）。
- UI backend のログDB参照を `logs_root()` に統一（`apps/ui-backend/backend/main.py`）。
- 検証: `python3 -m pytest -q tests/test_paths.py tests/test_llm_router.py tests/test_llm_client.py packages/commentary_02_srt2images_timeline/tests/test_orchestration.py`
- Git備考: この環境では `.git` への新規書込みが拒否されるため、差分はパッチとして `backups/patches/*_stage2_cues_plan_paths.patch`, `backups/patches/*_stage3_capcut_tools.patch` に保存。

## 2025-12-14
- A/B/音声/SRT/run の“正本”を迷わないため、Aテキスト（assembled_human優先）とAudio final（a_text/b_text含む）をSSOTへ明記（`ssot/ops/OPS_SCRIPT_SOURCE_MAP.md`, `workspaces/scripts/README.md`）。
- エピソード単位の1:1管理ツールを追加（`scripts/episode_ssot.py`）。`metadata.video_run_id` の自動/手動設定と `workspaces/episodes/{CH}/{NNN}/`（symlink + manifest）生成を提供。
- 生成物ライフサイクルの run 採用SoTを `status.json.metadata.video_run_id` に統一（`ssot/plans/PLAN_OPS_ARTIFACT_LIFECYCLE.md`）。
- `workspaces/episodes/` をgitignoreしつつ README は保持（`workspaces/.gitignore`, `workspaces/README.md`, `workspaces/episodes/README.md`）。

## 2025-12-15
- CH06のCapCutドラフト混乱（音声/字幕不一致・完成版不明）を解消するため、run_dir を final SRT に再整合し、ドラフトへ音声WAV/字幕を manifest 正本から再注入（`packages/commentary_02_srt2images_timeline/tools/align_run_dir_to_tts_final.py`, `packages/commentary_02_srt2images_timeline/tools/patch_draft_audio_subtitles_from_manifest.py`）。
- 壊れた `capcut_draft` symlink を修復し、欠損していた CH06-031/032/033 のドラフトを再生成。Planning CSV の CH06-031 タイトル不整合も修正し、命名/参照のブレを抑制。CH06-テンプレの汚染（srt2images/subtitles/voiceover残骸）を除去し、再生成を安定化。
- CH06 の採用 run を `status.json.metadata.video_run_id` に固定し、未採用 run を `workspaces/video/_archive/` へ追加退避（詳細は `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`）。
- `workspaces/video/input` の同期を「既存でも不一致なら退避→更新」に修正し、stale SRT/WAV を一括で正本（audio/final）へ揃えた（`packages/commentary_02_srt2images_timeline/tools/sync_audio_inputs.py`, 記録: `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`）。
- 既存CapCutドラフトの画像差し替え（再生成）で run_dir 推定が誤る問題を修正し、draft/run_dir 不一致は明示エラーにした（`apps/ui-backend/backend/routers/swap.py`）。併せて、既存ドラフトの validator が落ち続けないように foreign tracks をデフォルトWARN化し、必要時のみ strict で落とせるようにした（`packages/commentary_02_srt2images_timeline/tools/validate_srt2images_state.py`）。
- UIフリーズ対策: SwapImagesPage の画像を「手動ロード + 表示件数制限/カット指定 + lazy + サムネ」に変更し、backend 側も `/api/swap/images/file?max_dim=` でサムネを返せるようにした（`apps/ui-frontend/src/pages/SwapImagesPage.tsx`, `apps/ui-backend/backend/routers/swap.py`）。AudioReviewPage の `<audio preload>` を `none` にして一覧表示で大量 `/audio` Range が走らないようにした（`apps/ui-frontend/src/components/AudioReviewPage.tsx`）。
- 検証: `python3 -m py_compile apps/ui-backend/backend/routers/swap.py` / `npm -C apps/ui-frontend run build`

## 2025-12-16
- 全チャンネル共通の読み台本（Aテキスト）品質ルールをSSOT化（`ssot/ops/OPS_A_TEXT_GLOBAL_RULES.md`）。`---` のみをポーズ挿入として許可し、`「」/（）` 多用・冗長/反復・URL/脚注混入などの事故要因を固定で禁止。
- 台本運用SSOTを更新し、Aテキストのグローバルルール参照と区切り記号の扱いを明文化（`ssot/ops/OPS_SCRIPT_GUIDE.md`, `ssot/reference/【消さないで！人間用】確定ロジック.md`）。
- チャンネルの固定コンテキストを1つに寄せるため、`configs/sources.yaml` を拡張し、planning/persona に加えて `channel_prompt` / `chapter_count` / 文字数目安を登録（全CHの参照点を統一）。
- script_pipeline の sources 読み込みを `configs/sources.yaml` 優先に切替え、既存 status.json にも欠損メタ（style/persona/prompt/表示名等）を安全に補完できるようにした（`packages/script_pipeline/runner.py`, `packages/script_pipeline/config/sources.yaml`）。
- 章生成プロンプトをTTS前提の自然さに寄せ、強制的な問いかけ/比喩のノルマを撤廃し、グローバルAテキストルールを注入（`packages/script_pipeline/prompts/chapter_prompt.txt`）。
- TTSのポーズマーカーを `---` のみに限定（`packages/audio_tts_v2/tts/strict_segmenter.py`）。
- Aテキストの品質チェック/拡張のための運用スクリプトを追加（`scripts/lint_a_text.py`, `scripts/expand_a_text.py`）。

## 2025-12-17
- `audio_sync_status.json` を code階層（packages）から排除し、状態ファイルとして `workspaces/video/_state/` に移設（`factory_common.paths.video_audio_sync_status_path()` + `packages/commentary_02_srt2images_timeline/tools/sync_audio_inputs.py`）。差分ノイズと誤参照を削減。
- CapCut運用ツールを整理/強化（`packages/commentary_02_srt2images_timeline/tools/*`）。画像スケール適用の点検ツールを追加（`capcut_apply_image_scale.py`）。
- Planning SoT を更新（`workspaces/planning/channels/CH02.csv`, `CH05.csv`, `CH06.csv`, `CH07.csv`）。
- 投稿済みロックを追加: `進捗=投稿済み` を最終固定とし、UI（企画CSV詳細）から `投稿済みにする（ロック）` をワンクリック実行できるようにした。内部APIは `POST /api/channels/{CH}/videos/{NNN}/published`（`packages/factory_common/publish_lock.py`, `apps/ui-backend/backend/main.py`, `apps/ui-frontend/src/pages/PlanningPage.tsx`）。
- Cleanup（Remotion）: `apps/remotion/` 配下の未使用サンプルrun資産（画像/JSON）を archive-first 後に削除（記録: `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`）。
- Planning CSV の絶対パスを repo 相対へ正規化（CH01/02/03/05-11。CH04はロック中で保留）。`scripts/sync_all_scripts.py` / `scripts/sync_ch02_scripts.py` も今後は相対パスを書き出すよう更新。
- ドキュメント/運用例の `/Users/dd/...` を除去し、`<REPO_ROOT>` へ置換（`README.md`, `scripts/cleanup_data.md`, `scripts/youtube_publisher/README.md`, `ssot/ops/OPS_TTS_MANUAL_READING_AUDIT.md` など）。
- `.gitignore` を整理: JSON を一律 ignore しない方針へ修正し、Remotionの生成物/ローカルキャッシュ（`apps/remotion/{input,out}` 等）と `data/visual_bible*.json` は個別に ignore。
- 検証: `npm -C apps/ui-frontend run build` / `python3 -m py_compile apps/ui-backend/backend/main.py` / `python3 -m py_compile scripts/sync_all_scripts.py scripts/sync_ch02_scripts.py`
- レガシー削除: `scripts/maintain_consciousness.py` を archive-first で削除し、`ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md` に記録（Step 39）。
- Planning テンプレも正規化: `workspaces/planning/templates/CH07..CH10_planning_template.csv` の台本パスを `workspaces/scripts/...`（repo相対）へ修正。
- SSOT追加: `ssot/ops/master_styles.json` を repo に追加し、CapCutスタイル/タイムライン設定の正本を明示（`style_resolver.py` の default）。
- 新チャンネル立ち上げ: CH12–CH16 の planning CSV + persona と、まとめて init/メタ補完する `scripts/buddha_senior_5ch_prepare.py` を追加（`workspaces/planning/buddha_senior_5ch_setup.md`）。
- 画像系プロンプトの絶対パス除去: `packages/commentary_02_srt2images_timeline/system_prompt_for_image_generation.txt` の repo root 記載を `<REPO_ROOT>` に置換。
- CH12–CH16: UI/台本ライン用のチャンネル情報（script_prompt/channel_info）を追加し、YouTubeハンドルを `@buddha-a001`〜`@buddha-e001` で登録（`packages/script_pipeline/channels/CH12-*`〜`CH16-*`, `configs/sources.yaml`, `workspaces/planning/buddha_senior_5ch_setup.md`）。
- 新規チャンネル追加の入口を統一: YouTubeハンドル(@name)→一意特定→スキャフォールド生成を `python3 -m script_pipeline.tools.channel_registry create ...` と UI `/channel-settings`（`POST /api/channels/register`）で提供。OpenGraphからチャンネル名/アイコンも取得し、APIキー/検索のブレを回避（`packages/factory_common/youtube_handle.py`, `apps/ui-backend/backend/main.py`, `apps/ui-frontend/src/pages/ChannelSettingsPage.tsx`, `ssot/ops/OPS_CHANNEL_LAUNCH_MANUAL.md`）。
- LLM/音声ガード強化: `LLM_FORCE_MODELS` / `LLM_FORCE_TASK_MODELS_JSON` による実行時モデル上書きと、`script_pipeline.cli`/`audio_tts_v2.scripts.run_tts` の `--llm-model/--llm-task-model` 対応を追加。`run_tts` は `script_validation` 未完了なら停止（`--allow-unvalidated` で例外）。SSOTも追記（`ssot/ops/OPS_LLM_MODEL_CHEATSHEET.md`, `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`）。
- 事故防止: Planning↔Script の整合が疑わしい場合は alignment stamp を「suspect(ハッシュ無し)」で記録し、`script_validation`/TTS が確実に止まるようにした（`packages/factory_common/alignment.py`, `scripts/enforce_alignment.py`, `packages/script_pipeline/runner.py`）。併せて `script_pipeline.cli audio` は assembled_human しか無い場合に assembled.md を自動生成するよう修正。テスト追加: `tests/test_alignment.py`, `tests/test_llm_router.py`。
- Video runs 整理: `scripts/ops/cleanup_video_runs.py` を追加し、`scripts/cleanup_workspace.py --video-runs` から run dir を削除せず `_archive/` へ移動できるようにした。実行記録: `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`（Step 43-45）。
- レガシー削除: `packages/commentary_02_srt2images_timeline/ui/`（互換shim）を archive-first 後に削除し、「どっちが正本？」の混乱を低減（記録: `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md` Step 46）。
- Aテキスト品質: `%/％/パーセント` を統計捏造の入口として禁止し、`script_validation` で確実に落とすガードを追加（`packages/script_pipeline/validator.py`, `tests/test_a_text_validator.py`, `ssot/ops/OPS_A_TEXT_GLOBAL_RULES.md`）。CH07の prompt も同方針に追従。
- UI改善: `/agent-org` を tabs + 検索 + auto-refresh で見やすく刷新し、チャンネル未選択時に `/channel-settings` への「新規登録」導線を追加（`apps/ui-frontend/src/pages/AgentOrgPage.tsx`, `apps/ui-frontend/src/pages/ChannelOverviewPage.tsx`, `apps/ui-frontend/src/pages/ChannelSettingsPage.tsx`）。
- Video runs 復旧/完全整理: `scripts/ops/restore_video_runs.py` を追加し、`archive_report.json` から run dir を確実に戻せるようにした。unscoped/legacy run（numeric/api/jinsei/ui_* 等）を `_archive/` へ退避して `workspaces/video/runs/` のディレクトリを episode-keyed のみに整理（記録: `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md` Step 47-48, 正本: `ssot/ops/OPS_VIDEO_RUNS_ARCHIVE_RESTORE.md`）。
- Audio 辞書参照の正本化: hazard 辞書を `repo_root()/data/hazard_readings.yaml` 優先で読むよう修正し、二重管理による“効いてない辞書”問題を解消（`packages/audio_tts_v2/tts/risk_utils.py`, `packages/audio_tts_v2/tests/test_risk_utils.py`）。
- UI改善: `/planning` を `?channel=CHxx` で深リンクできるようにし、サイドバーも選択チャンネルに追従（`apps/ui-frontend/src/pages/PlanningPage.tsx`, `apps/ui-frontend/src/layouts/AppShell.tsx`）。

## 2025-12-17
- UI改善: ダッシュボード/サイドバーのチャンネル一覧に「＋追加」導線を追加し、`/channel-settings?add=1` で新規チャンネル登録へ直行できるようにした（`apps/ui-frontend/src/pages/DashboardPage.tsx`, `apps/ui-frontend/src/components/ChannelListSection.tsx`）。
- UI改善: SoT パス表記を `workspaces/` 正本に統一し、互換パスは補足表記に寄せた（`apps/ui-frontend/src/layouts/AppShell.tsx`, `apps/ui-frontend/src/pages/ProjectsPage.tsx`, `apps/ui-frontend/src/pages/ScriptFactoryPage.tsx`, `apps/ui-frontend/src/pages/AutoDraftPage.tsx`, `apps/ui-frontend/src/components/VideoProductionWorkspace.tsx`, `apps/ui-frontend/src/components/VideoDetailPanel.tsx`, `apps/ui-frontend/src/pages/EpisodeStudioPage.tsx`, `apps/ui-frontend/src/components/AudioWorkspace.tsx`）。
- UI改善: TTS周りのハードコード（CH02/CH04/CH06固定・既定選択）を廃止し、動的なチャンネル一覧 + 明示選択に変更（`apps/ui-frontend/src/pages/AudioTtsV2Page.tsx`, `apps/ui-frontend/src/components/BatchTtsProgressPanel.tsx`, `apps/ui-frontend/src/components/AudioReviewPage.tsx`）。
- UI改善: 企画CSV（`/planning`）を `?channel=CHxx&video=NNN` で深リンクし、行詳細の自動オープン/URL同期と「案件ページへ」導線を追加（`apps/ui-frontend/src/pages/PlanningPage.tsx`）。
- 検証: `npx -C apps/ui-frontend tsc -p tsconfig.json --noEmit`
- UI改善: 「企画CSV」サイドバーリンクは常に未選択状態で開くようにし、ダッシュボードのチャンネルカードはクリックで案件ページ（`/channels/CHxx`）へ確実に遷移するようにした（`apps/ui-frontend/src/layouts/AppShell.tsx`, `apps/ui-frontend/src/pages/DashboardPage.tsx`）。
- UI改善: ダッシュボードに「制作フロー I/O」カード（企画/台本/音声/動画/サムネ）を追加し、正本パスと入口を同時に把握できるようにした（`apps/ui-frontend/src/pages/DashboardPage.tsx`）。
- UI改善: `/agent-org` は `?tab=&from=&q=&auto=` をURL同期し、リンクコピー/locksに created_by 表示を追加して協調運用を迷わない形にした（`apps/ui-frontend/src/pages/AgentOrgPage.tsx`）。
- UI改善: チャンネル登録カードの説明文をパス誤解が起きない表現へ修正（`apps/ui-frontend/src/pages/ChannelSettingsPage.tsx`）。
- 修正: `ThumbnailWorkspace` の generate dialog state 初期化に必須フィールド（`sourceTitle`/`thumbnailPrompt`）を追加し、フロントの型チェック失敗でUIが起動不能になる事故を防止（`apps/ui-frontend/src/components/ThumbnailWorkspace.tsx`）。
- UI改善: 企画CSVの行詳細から `制作フロー` / `Studio` へ直行ボタンを追加し、企画→実行の往復を1クリックに短縮（`apps/ui-frontend/src/pages/PlanningPage.tsx`）。
- UI改善: `制作フロー` / `Episode Studio` で「企画CSVを開く」導線を追加し、企画（SoT）→台本/音声/動画の往復を迷わない形にした（`apps/ui-frontend/src/pages/WorkflowPage.tsx`, `apps/ui-frontend/src/pages/EpisodeStudioPage.tsx`）。
- UI改善: 案件ページ（VideoDetail）に `企画CSV/制作フロー/Studio/CapCut/サムネ` のクイックリンクを追加（`apps/ui-frontend/src/components/VideoDetailPanel.tsx`）。
- UI改善: `TTS音声生成` は `?channel=CHxx` をURL同期し、選択状態を共有/復帰しやすくした（`apps/ui-frontend/src/pages/AudioTtsV2Page.tsx`）。`制作フロー`/`Studio` からは `channel` 付きで遷移。
- UI改善: サムネページ上部に SoT 表示と主要導線を追加（`apps/ui-frontend/src/pages/ThumbnailsPage.tsx`）。
- 検証: `npm -C apps/ui-frontend run build`

## 2025-12-18
- scripts 起動の安定化: `scripts/_bootstrap.py` を導入し、`Path(__file__).parents[...]` の直書きを `pyproject.toml` 探索ベースへ統一（scripts/ops も同様）。`workspaces/logs` への出力は `factory_common.paths.logs_root()` を優先。
- Remotion preview 入力を正本へ: `apps/remotion/input` を `workspaces/video/input` へ symlink し、`apps/remotion/public/input` から正しい入力が参照されるようにした。
- logs の分散抑制: `scripts/validate_status_sweep.py` の timestamped レポートを `workspaces/logs/regression/validate_status/` へ集約し、`workspaces/logs/validate_status_full_latest.json` を latest として維持（SSOT: `ssot/ops/OPS_LOGGING_MAP.md`）。
- 旧Qwen/core-tools導線の誤参照を解消: `packages/script_pipeline/prompts/{phase2_audio_prompt,orchestrator_prompt}.txt` を現行CLI/SoT/agent_orgメモ運用に更新し、`README.md` から `QWEN.md` 参照を削除。
- SRT/音声監査のSoTを final 基準に統一: `scripts/verify_srt_sync.py` と `scripts/audio_integrity_report.py` を `workspaces/audio/final/` 参照へ更新し、`scripts/check_all_srt.sh` は旧フラグを廃止して検査ログを `workspaces/logs/regression/srt_validation/` に集約（SSOT: `ssot/ops/OPS_LOGGING_MAP.md`）。
- 確実ゴミ削除（archive-first）: CH10-001 固定の one-off 再生成スクリプト（`scripts/regenerate_audio.py`, `scripts/regenerate_strict.py`）を退避した上で repo から削除し、棚卸しと実行ログを更新（`ssot/reference/REFERENCE_PATH_HARDCODE_INVENTORY.md`, `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`）。
- 確実ゴミ削除（archive-first）: 旧Route audio の deprecation stub（`scripts/_core_audio.py`, `scripts/run_route1_batch.py`, `scripts/run_route2_agent.py`）を退避した上で削除（実行しても exit するだけの探索ノイズのため）。記録: `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`。
- 確実ゴミ削除（archive-first）: 実行不能で参照ゼロの `legacy/scripts/route_audio/` を tar 退避した上で削除（探索ノイズ削減）。記録: `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`。
- 確実ゴミ削除（archive-first）: 旧 `commentary_02` の Legacy UI/設定（`legacy/commentary_02_srt2images_timeline/`）を tar 退避した上で削除し、差し替えUIドキュメントを現行 React UI（`/capcut-edit/swap`）へ更新。記録: `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`。
- 確実ゴミ削除（archive-first）: 参照ゼロの `legacy/scripts/agent_coord.py` を退避した上で削除（協調運用は `scripts/agent_org.py` が正本）。記録: `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`。
- UI改善: サムネの「量産（Canva）」を実運用向けに強化（BulkPanel + channel_style の表示/ルール提示）。フロントの TS build エラー（hook順/型）を解消し、テンプレ正本（`workspaces/thumbnails/templates.json`）の style 情報を API で返すようにした（`apps/ui-backend/backend/main.py`, `apps/ui-frontend/src/components/ThumbnailWorkspace.tsx` ほか）。検証: `npm -C apps/ui-frontend run build`。
- 運用改善: 壊れた `capcut_draft` symlink の掃除ツールを追加し、coordination lock が symlink でも効くように修正（`scripts/ops/cleanup_broken_symlinks.py`, `packages/factory_common/locks.py`）。掃除レポートは `workspaces/logs/regression/broken_symlinks/`。
- 運用改善: `workspaces/episodes/` の materialize を「存在しないリンクは残さない」挙動に変更し、壊れたsymlinkを掃除してリンク集のノイズを低減（`scripts/episode_ssot.py`, `workspaces/episodes/README.md`）。
- lock衛生: `python scripts/agent_org.py locks-prune` を追加し、期限切れ lock JSON を `workspaces/logs/agent_tasks/coordination/locks/_archive/YYYYMM/` に退避できるようにした（`scripts/agent_org.py`, `ssot/ops/OPS_AGENT_PLAYBOOK.md`, `ssot/ops/OPS_LOGGING_MAP.md`）。検証: `python -m py_compile scripts/agent_org.py`, `python scripts/ops/ssot_audit.py --strict`（commit `8d9cff1b`）。
- 共同運用: 複数エージェントが **1ファイル** に状態/申し送りを書き込める Shared Board を追加（`python scripts/agent_org.py board show|set|note`）。実体: `workspaces/logs/agent_tasks/coordination/board.json`（`scripts/agent_org.py`, `ssot/ops/OPS_AGENT_PLAYBOOK.md`, `ssot/ops/OPS_LOGGING_MAP.md`, `ssot/ops/OPS_ENTRYPOINTS_INDEX.md`）。
- 共同運用の精度向上: Board note に `note_id` を付与し全文表示（`board note-show`）を追加。さらに `board template` と BEP-1（共通記法）をSSOT化し、zshの展開事故を避ける投稿方法（`<<'EOF'`）を標準化（`scripts/agent_org.py`, `ssot/ops/OPS_AGENT_PLAYBOOK.md`）。
- 共同運用の拡張: Board に ownership（`board areas` / `board area-set`）と thread（返信 `--reply-to`, `board threads` / `board thread-show`）を追加し、「誰がどの処理担当か」「レビュー/コメントのスレッド追跡」を1枚で運用できるようにした。`overview` でも board 状態（doing/blocked/next）が見える（`scripts/agent_org.py`, `ssot/ops/OPS_AGENT_PLAYBOOK.md`, `ssot/ops/OPS_ENTRYPOINTS_INDEX.md`, `ssot/ops/OPS_LOGGING_MAP.md`）。
- 互換: 過去の board 投稿に `note_id` が無い場合でも追跡できるように `board normalize` を追加（legacy note に id/thread_id を付与）。`board show` は legacy 検知時に警告を出す（`scripts/agent_org.py`, `ssot/ops/OPS_AGENT_PLAYBOOK.md`）。

## 2025-12-21
- CH12–CH16（ブッダ系シニア5ch）: `channel_info.json` に `youtube_description` と `default_tags` を追加し、誤って入っていた `script_prompt` のパス文字列を除去（prompt は `script_prompt.txt` を正として読ませる）。
- CH12: 台本プロンプトは「物語先行型（4部構成）」が正本のため、`script_prompt.txt` は維持（誤変更が入った場合は復元）: `packages/script_pipeline/channels/CH12-ブッダの黄昏夜話/script_prompt.txt`。
- CH12–CH16: 音声設定（VOICEVOX「青山龍星」）の `voice_config.json` を追加: `packages/script_pipeline/audio/channels/CH12..CH16/voice_config.json`。
- CH12–CH16: チャンネル一覧 JSON を再生成: `packages/script_pipeline/channels/channels_info.json`。
- CH12: ベンチマーク正（物語先行4部構成）に揃えるため、誤って混入していた「8パート固定」前提を除去（`scripts/buddha_senior_5ch_prepare.py`, `workspaces/planning/personas/CH12_PERSONA.md`）。既存 `workspaces/scripts/CH12/001..030/status.json` もメタを patch（chapter_count=4 等）。
- Cleanup（archive-first）: 誤誘導の温床になっていた `workspaces/planning/buddha_senior_5ch_setup.md` をアーカイブして削除（`backups/graveyard/20251221T072310Z__workspaces_planning_buddha_senior_5ch_setup_md.tar.gz`）。記録: `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`。
- SSOT更新: `ssot/ops/OPS_SCRIPT_GUIDE.md` に超長尺（Marathon）入口・検証・`SCRIPT_VALIDATION_LLM_QUALITY_GATE=0` の運用を追記。
- SSOT更新: `ssot/ops/OPS_LONGFORM_SCRIPT_SCALING.md` に Route C（Marathon）と Marathon v1 の I/O（plan/invalid/assembled_candidate/validation）と、未実装の Memory/チャンクJudge を明文化。
- Marathon改善: ブロック雛形（章の箱）を `configs/longform_block_templates.json` に外出しし、`a_text_marathon_compose.py` に `--block-template` を追加（CH別の流儀を固定しやすくした）。
- 新チャンネル立ち上げ（YouTube handle未確定のため offline scaffold）: CH17–CH21 のチャンネル資材を追加（`packages/script_pipeline/channels/CH17-*`〜`CH21-*` に `channel_info.json`/`script_prompt.txt`、`packages/script_pipeline/audio/channels/CH17..CH21/voice_config.json`、`workspaces/scripts/CH17..CH21/`、`workspaces/planning/channels/CH17..CH21.csv`、`workspaces/planning/personas/CH17..CH21_PERSONA.md`）。
- `configs/sources.yaml` を更新: CH12 の `chapter_count` をベンチマーク正（4部構成）に揃えて 4 に修正。CH17–CH21 を登録（`chapter_count=7`, `target_chars_min=18000`, `target_chars_max=26000`）。
- チャンネル一覧 JSON を再生成: `packages/script_pipeline/channels/channels_info.json`（CH17–CH21 を反映）。

## 2025-12-22
- healthcheck の収束: `python3 apps/ui-backend/tools/start_manager.py healthcheck --with-guards` の prompt audit が生成ログ由来で落ちる問題を解消（guard は `scripts/prompt_audit.py --skip-scripts` を実行するよう変更）。
- `scripts/prompt_audit.py` 改善: timezone-aware timestamp / registry-path の file 限定 / 重複pathのdedupe / script側は canonical surfaces（`assembled*.md`, `audio_prep/script_sanitized*.txt`）のみ監査。
- SSOT更新: prompt audit の運用入口を明確化（`ssot/ops/OPS_SCRIPTS_PHASE_CLASSIFICATION.md`）。棚卸しを再生成（`python3 scripts/ops/scripts_inventory.py --write` → `ssot/ops/OPS_SCRIPTS_INVENTORY.md`）。
- 検証: `python3 apps/ui-backend/tools/start_manager.py healthcheck --with-guards` / `python3 scripts/ops/ssot_audit.py --strict`。

## 2025-12-23
- CH17–CH21（睡眠系ch）: テーマ誤りを修正し、チャンネル資材（`channel_info.json`/`script_prompt.txt`）を新コンセプトへ更新（`packages/script_pipeline/channels/CH17-*`〜`CH21-*`, `packages/script_pipeline/channels/channels_info.json`, `configs/sources.yaml`）。
- CH17–CH21: Planning CSV と Persona をリセット/再シード（`workspaces/planning/channels/CH17..CH21.csv`, `workspaces/planning/personas/CH17..CH21_PERSONA.md`）。
- 新チャンネル追加: CH22/CH23 の channel assets + Planning CSV/Persona + ベンチマークメモを追加（`packages/script_pipeline/channels/CH22-*`〜`CH23-*`, `workspaces/planning/channels/CH22.csv`, `workspaces/planning/channels/CH23.csv`, `workspaces/planning/personas/CH22_PERSONA.md`, `workspaces/planning/personas/CH23_PERSONA.md`, `workspaces/research/benchmarks/kokoroshiawase.md`, `configs/sources.yaml`, `packages/script_pipeline/channels/channels_info.json`）。
- script_validation: Fixer に「原文80%維持/指摘箇所優先」を明示し、字数救済後に quote/paren/pause 禁則を再サニタイズして収束性を改善（`packages/script_pipeline/prompts/a_text_quality_fix_prompt.txt`, `packages/script_pipeline/runner.py`）。
- UI backend: channel audit/summary が `youtube_handle` と `template_path` にも対応し、CH22/CH23 の未同期状態でもリンク/存在チェックが正しく出るようにした（`apps/ui-backend/backend/main.py`）。
- 検証: `pytest -q tests/test_script_pipeline_runner_import.py tests/test_script_validation_llm_gate_skip.py tests/test_youtube_handle_resolver.py`

## 2025-12-24
- UI: チャンネルごとのポータルページを追加（`/channels/:channelCode/portal`）。チャンネル設定（ハンドル/説明/既定タグ/LLMモデル/台本プロンプト）と企画一覧、動画プレビューを1画面に集約（`apps/ui-frontend/src/pages/ChannelPortalPage.tsx`）。
- UI: portal ルートでチャンネル選択が同期されるよう `AppShell` のルート判定を拡張（`apps/ui-frontend/src/layouts/AppShell.tsx`）。ルーティングを追加（`apps/ui-frontend/src/App.tsx`）。
- UI: チャンネル概要カードから「ポータル」「チャンネル設定」へ直行リンクを追加（`apps/ui-frontend/src/components/ChannelOverviewPanel.tsx`）。
- UI: サイドバー（主要メニュー）に「チャンネルポータル」を追加し、選択中チャンネルのポータルへ直行できるようにした（`apps/ui-frontend/src/layouts/AppShell.tsx`）。
- UI: ポータル上部に「チャンネルアイコン切り替えバー」とクイック導線（案件一覧/チャンネル設定/企画CSV/YouTube/管理シート）を追加し、企画一覧/プレビューはスクロール分離で見失いにくくした（`apps/ui-frontend/src/pages/ChannelPortalPage.tsx`, `apps/ui-frontend/src/pages/ChannelPortalPage.css`）。
- UI: ポータルのUXを追加調整。チャンネル切替に検索を追加し、カードhoverの位置ズレを抑止して重なりを解消。企画一覧は全幅表示 + 企画番号を数値昇順に揃えた（`apps/ui-frontend/src/pages/ChannelPortalPage.tsx`, `apps/ui-frontend/src/pages/ChannelPortalPage.css`）。
- UI: 台本作成/一括処理（`/projects`, `/projects2`）で、チャンネルprofileのデフォルト値が既定LLMモデルを誤って上書きする問題を修正。LLM設定（`script_rewrite`）を優先し、fallback（`qwen/qwen3-14b:free`）は上書きしない（`apps/ui-frontend/src/pages/ProjectsPage.tsx`, `apps/ui-frontend/src/pages/ScriptFactoryPage.tsx`）。
- UI: ポータル/ダッシュボードの長い文字列（パス等）が枠からはみ出す問題を修正し、ポータルでは「パス表示」ではなく SSOT/テンプレ内容（persona / planning template / 画像プロンプトテンプレ）を本文で確認できるようにした（`apps/ui-frontend/src/pages/ChannelPortalPage.tsx`, `apps/ui-frontend/src/pages/ChannelPortalPage.css`, `apps/ui-frontend/src/App.css`）。
- 検証: `npm -C apps/ui-frontend run build`
- script_validation: タイトル/サムネ訴求↔Aテキスト本文の**意味整合ゲート**を追加し、`verdict=major` は pending で停止するようにした（`packages/script_pipeline/runner.py`）。レポートは `content/analysis/alignment/semantic_alignment.json`、メタは `status.json: metadata.semantic_alignment`。長尺は `SCRIPT_SEMANTIC_ALIGNMENT_MAX_A_TEXT_CHARS` 超過でスキップ可。
- script_outline: 章草稿生成（高コスト）に入る前に、アウトライン段階で意味整合の事前ゲートを実行（`content/analysis/alignment/outline_semantic_alignment.json`）。`major` は `script_outline` を pending 停止（`packages/script_pipeline/runner.py`）。
- Planning混線（tag_mismatch）: 早期停止のオプションを追加（`SCRIPT_BLOCK_ON_PLANNING_TAG_MISMATCH=1`）。lint側も `--tag-mismatch-is-error` で exit 非0 化できるようにした（`scripts/ops/planning_lint.py`）。
- SSOT更新: 意味整合が `script_outline`/`script_validation` の確定ゲートになったこと、Planning混線のstrict運用オプションを追記（`ssot/ops/OPS_SCRIPT_PIPELINE_SSOT.md`, `ssot/ops/OPS_SEMANTIC_ALIGNMENT.md`, `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`, `ssot/ops/OPS_PLANNING_CSV_WORKFLOW.md`, `ssot/ops/OPS_SCRIPT_INPUT_CONTRACT.md`, `ssot/ops/OPS_A_TEXT_LLM_QUALITY_GATE.md`）。
- 検証: `.venv/bin/pytest -q tests/test_planning_lint_tag_mismatch_strict.py tests/test_script_validation_llm_gate_skip.py tests/test_script_pipeline_runner_import.py`

## 2025-12-25
- runner修復: `packages/script_pipeline/runner.py` の SyntaxError/Tab混入で import/実行が不安定だったため、インデント崩れを修正してコンパイル可能な状態に復旧。
- script_validation（長さ収束）:
  - `length_too_long` で Shrink を実行しても削りが足りないケースがあったため、`---` 区切り単位の決定論トリム（`deterministic_budget_trim`）をフォールバックとして追加し、必ずレンジ内へ収束するようにした（証跡: `status.json: stages.script_validation.details.auto_length_fix_fallback`）。
  - `length_too_short` の「あと少し足りない」事故を減らすため、事前救済を最大3パスに拡張（3パス目は残り不足 `<=1200` の場合のみ）してコスト暴走を防止した。
- SSOT更新: 文字数収束（Expand 3rd pass 条件 / Shrinkの決定論フォールバック）を追記（`ssot/ops/OPS_A_TEXT_LLM_QUALITY_GATE.md`, `ssot/ops/OPS_SCRIPT_PIPELINE_SSOT.md`）。
- 検証（運用）:
  - 既存: `scripts/ops/script_runbook.py resume --channel CH07 --video 019 --until script_validation` → `script_validated`（semantic_alignment verdict `ok`）。
  - 新規: `scripts/ops/script_runbook.py new --channel CH10 --video 007` → `resume --until script_validation` で `script_validated`（semantic_alignment verdict `minor`）。
- 検証（技術）: `python3 -m compileall -q packages/script_pipeline/runner.py`, `.venv/bin/pytest -q tests/test_script_pipeline_runner_import.py tests/test_script_validation_llm_gate_skip.py tests/test_planning_lint_tag_mismatch_strict.py tests/test_semantic_alignment_policy.py`

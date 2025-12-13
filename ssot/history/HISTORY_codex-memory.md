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
- ログ整理の導線を追加: `scripts/ops/cleanup_logs.py`（L3 logs ローテ）, `scripts/cleanup_data.py` を dry-run 既定 + keep-days ガードに更新し、古い script_pipeline logs を削除（記録: `ssot/OPS_CLEANUP_EXECUTION_LOG.md`）。
- UI backend のログDB参照を `logs_root()` に統一（`apps/ui-backend/backend/main.py`）。
- 検証: `python3 -m pytest -q tests/test_paths.py tests/test_llm_router.py tests/test_llm_client.py commentary_02_srt2images_timeline/tests/test_orchestration.py`
- Git備考: この環境では `.git` への新規書込みが拒否されるため、差分はパッチとして `backups/patches/*_stage2_cues_plan_paths.patch`, `backups/patches/*_stage3_capcut_tools.patch` に保存。

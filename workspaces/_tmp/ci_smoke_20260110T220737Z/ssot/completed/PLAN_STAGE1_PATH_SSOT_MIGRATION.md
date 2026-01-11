# PLAN_STAGE1_PATH_SSOT_MIGRATION — Path SSOT 導入の超細粒度移行計画（物理移動なし）

## Plan metadata
- **Plan ID**: PLAN_STAGE1_PATH_SSOT_MIGRATION
- **ステータス**: Closed
- **担当/レビュー**: Owner: dd / Reviewer: dd
- **対象範囲 (In Scope)**: `packages/factory_common/paths.py` 新設と、全実行コードの直書きパス置換（物理移動は含まない）
- **非対象 (Out of Scope)**: workspaces/legacy/apps/packages の物理移設、生成品質ロジック変更
- **関連 SoT/依存**: `ssot/plans/PLAN_REPO_DIRECTORY_REFACTOR.md`, `ssot/reference/REFERENCE_PATH_HARDCODE_INVENTORY.md`, `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`
- **最終更新日**: 2025-12-17

---

## 1. ゴール / 成功条件

### 1.1 ゴール
- **どのCWD/どの層から実行しても同一のパス解決結果になる**「Path SSOT」を導入し、以後の物理移設の修正範囲を最小化する。
- 旧パス構造のままでも動作を壊さない（Stage 1 は物理移動ゼロ）。

### 1.2 DoD (Stage1)
- `packages/factory_common/paths.py` が存在し、主要 getter が実装されている。
- `ssot/reference/REFERENCE_PATH_HARDCODE_INVENTORY.md` の **Active/実行コード**の直書きパスが全て paths SSOT 経由になっている。
- 旧絶対パス `/Users/dd/...` と旧名 `commentary_01_srtfile_v2` が、実行コード層から消えている（Docs/Legacy/生成物は残ってOK）。
- 主要入口の help/import smoke が通る（物理移動前のベースライン維持）:
  - `python -m script_pipeline.cli --help`
  - `PYTHONPATH=".:packages" python3 -m audio_tts.scripts.run_tts --help`
  - `PYTHONPATH=".:packages" python3 -m video_pipeline.tools.factory --help`
  - `scripts/start_all.sh start`（Remotion preview 失敗は non‑blocking）

---

## 2. Path SSOT の仕様（Stage1で確定させるAPI）

### 2.1 ルート検出
- `repo_root()`:
  - `pyproject.toml` を上方向に探索して決定。
  - `YTM_REPO_ROOT` があれば最優先。
- `workspace_root()`:
  - `YTM_WORKSPACE_ROOT` があればそれを使用。
  - 無ければ `repo_root()/workspaces` を返す（Stage1では存在しなくてもよい）。

### 2.2 ドメイン別 getter（Stage1で最小限を実装）
**Planning**
- `planning_root()` → `workspace_root()/planning`（現行SoT）
- `channels_csv_path(ch)` → `planning_root()/channels/{ch}.csv`
- `persona_path(ch)` → `planning_root()/personas/{ch}_PERSONA.md`

**Scripts**
- `script_pkg_root()` → `repo_root()/packages/script_pipeline`
- `script_data_root()` → `workspace_root()/scripts`（現行SoT）
- `video_root(ch, vid)` → `script_data_root()/{ch}/{vid}`
- `status_path(ch, vid)` → `video_root(ch, vid)/status.json`

**Audio**
- `audio_pkg_root()` → `repo_root()/packages/audio_tts`
- `audio_artifacts_root()` → `workspace_root()/audio`（現行SoT）
- `audio_final_dir(ch, vid)` → `audio_artifacts_root()/final/{ch}/{vid}`

**Video (CapCut)**
- `video_pkg_root()` → `repo_root()/packages/video_pipeline`
- `video_runs_root()` → `workspace_root()/video/runs`（現行SoT）
- `video_run_dir(run_id)` → `video_runs_root()/{run_id}`
- `video_input_root()` → `workspace_root()/video/input`（音声ミラー入力）

**Thumbnails**
- `thumbnails_root()` → `workspace_root()/thumbnails`（現行SoT）
- `thumbnail_assets_dir(ch, vid)` → `thumbnails_root()/assets/{ch}/{vid}`

**Logs**
- `logs_root()` → `workspace_root()/logs`（現行SoT）

### 2.3 禁止ルール（lint化前提）
- 直書き `Path("script_pipeline/data")` / `"audio_tts/artifacts"` / `"video_pipeline/output"` / `"progress/channels"` / `"thumbnails/assets"` の新規追加を禁止。
- `/Users/dd/...` の絶対パスは全層で禁止（Stage1で実行コードはゼロにする）。

---

## 3. 置換の超細粒度順序（依存/危険度順）

> 元リストは `ssot/reference/REFERENCE_PATH_HARDCODE_INVENTORY.md` を正本とし、Stage1は **Active/実行コードのみ**を対象にする。

### 3.0 Stage1-0: paths.py 新設（置換開始前）
1. `packages/factory_common/paths.py` を新設（現行位置のまま）。
2. `tests/test_paths.py` を追加（env override / pyproject探索 / 主要 getter の戻りを検証）。
3. import smoke: `python -c "from factory_common.paths import repo_root; print(repo_root())"`

### 3.1 Stage1-1: `script_pipeline` コア（最上流）
対象（直書き `script_pipeline/data` / `progress/channels` の解消）:
- `packages/script_pipeline/sot.py`
- `packages/script_pipeline/runner.py`
- `packages/script_pipeline/job_runner.py`
- `packages/script_pipeline/tools/planning_store.py`（channels dir解決）
- 実行サンプルは SSOTへ集約（`ssot/ops/OPS_SCRIPTS_PHASE_CLASSIFICATION.md`, `ssot/ops/OPS_ENTRYPOINTS_INDEX.md`）

ゲート:
- `python -m script_pipeline.cli status --channel CH01 --video 001`（dry/存在しない場合は import smoke だけでOK）

### 3.2 Stage1-2: `audio_tts`（Scriptの下流）
対象（直書き `script_pipeline/data` / `audio_tts/artifacts` / 絶対パスの解消）:
- `packages/audio_tts/scripts/run_tts.py`
- `packages/audio_tts/tts/*` の SoT/辞書/ログ参照（見つかったものから順に）
- Legacy 直書きがある `packages/audio_tts/legacy_archive/scripts/*` は **Stage3で legacy 隔離**するまで置換しない。

ゲート:
- `PYTHONPATH=".:packages" python3 -m audio_tts.scripts.run_tts --help`
- `python -m script_pipeline.cli audio --channel CH01 --video 001 --help`

### 3.3 Stage1-3: `video_pipeline`（CapCut主線）
対象（直書き `audio_tts/artifacts` / `commentary_02.../output` / 絶対パスの解消）:
- `packages/video_pipeline/src/srt2images/orchestration/pipeline.py`
- `packages/video_pipeline/tools/auto_capcut_run.py`
- `packages/video_pipeline/tools/factory.py`
- `packages/video_pipeline/ui/server/jobs.py`
- `packages/video_pipeline/tools/sync_audio_inputs.py`
- `packages/video_pipeline/tools/safe_image_swap.py`
- `packages/video_pipeline/tools/*`（analysis/maintenance含む、archive除外）

ゲート:
- `PYTHONPATH=".:packages" python3 -m video_pipeline.tools.factory --help`

### 3.4 Stage1-4: UI backend（paths SSOT への一本化）
対象（`script_pipeline/data` / `progress/channels` / `audio_tts/artifacts` / `thumbnails/assets` / `commentary_02/output` / Remotion preview path）:
- `apps/ui-backend/backend/main.py`
- `apps/ui-backend/backend/video_production.py`
- `apps/ui-backend/backend/routers/auto_draft.py`
- `apps/ui-backend/backend/routers/swap.py`
- `apps/ui-backend/backend/routers/tts_progress.py`
- `apps/ui-backend/tools/assets_sync.py`（backendと同じpathsを使う）

ゲート:
- `PYTHONPATH=".:packages:apps/ui-backend" .venv/bin/python -c "import backend.main as m; print('ok')"`（import smoke）
- `scripts/start_all.sh start`（バックエンド起動確認。preview失敗は許容）

### 3.5 Stage1-5: UI frontend（表示パスの整理）
対象（表示文字列/URL生成のための直書きパス除去）:
- `apps/ui-frontend/src/api/client.ts`
- `apps/ui-frontend/src/pages/*`（ScriptFactory/Projects/AutoDraft/Thumbnails/Remotion*）
- `apps/ui-frontend/src/components/*`（ResearchWorkspace/AudioWorkspace/ThumbnailWorkspace）

方針:
- **ローカルファイルパスをフロントで組み立てない**。backendが返す URL を優先。
- ただし `thumbnails/assets` の公開URLは backend mount が正本なので、`/thumbnails/assets/...` のURL文字列は残してよい（ファイルシステム参照はしない）。

ゲート:
- `npm test/build` は Stage5（apps移動後）にまとめて行う。

### 3.6 Stage1-6: ルート scripts/tools/bin（最後に一括）
対象:
- `scripts/*.py`, `tools/*.py`, `scripts/*.sh` の直書きパス置換。
- Legacy文脈が強いもの（旧名依存/旧SoT参照）は **Stage3で legacy 隔離後に更新**。

ゲート:
- `python scripts/check_env.py`
- `python scripts/youtube_publisher/publish_from_sheet.py --help`

### 3.7 Stage1-7: テストの更新 / 隔離
対象:
- `tests/*` のうち現行パイプライン対象は paths SSOT に追従。
- `commentary_01_srtfile_v2` 参照テストは archive-first で `backups/graveyard/` に退避し、repo から削除（実施済み: `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md` Step 18）。

ゲート:
- `pytest -q`（現行対象のみが通る状態を維持）

---

## 4. 失敗時のロールバック
- Stage1は物理移動が無いので、ロールバックは **git revert でパス置換だけ戻す**。
- 置換は「1ファイルずつ」→「import smoke」→「次へ」の順で進め、壊れた場所を局所化する。

---

## 5. Stage2 への引き継ぎ
- Stage1 完了時点で paths SSOT があるため、Stage2 の copy→mv→symlink は paths 側の root 変更だけで全層が追従できる。
- Stage2 の各 substep 実施前に、paths の root 返り先を **一時的に env override で切替**できるようにしておく（`YTM_WORKSPACE_ROOT`）。

# OPS_CLEANUP_EXECUTION_LOG — 実行した片付け（復元/再現可能な記録）

このログは「実際に実行した削除/移動/退避」を、後から追跡できるように記録する。  
大原則: 破壊的操作は **バックアップ→削除/移動→SSOT更新** の順で行う。

---

## 2025-12-12

### 1) 確実ゴミの削除（repo tracked）

- 削除: `factory_commentary.egg-info/`（setuptools生成物）
  - 実行: `git rm -r factory_commentary.egg-info`
- 削除: `commentary_02_srt2images_timeline/src/memory/`（操作ログ残骸）
  - 実行: `git rm -r commentary_02_srt2images_timeline/src/memory`
  - 判定: コード参照ゼロ（`rg "operation_log.jsonl|subagent_contributions.jsonl|integration_summary.json" -S .`）
- 削除: `commentary_02_srt2images_timeline/ui/src/memory/`（操作ログ残骸）
  - 実行: `git rm -r commentary_02_srt2images_timeline/ui/src/memory`
  - 判定: コード参照ゼロ（同上）
- 削除: `commentary_02_srt2images_timeline/**/runtime/logs/notifications.jsonl`（通知ログのコミット残骸）
  - 実行:
    - `git rm commentary_02_srt2images_timeline/src/runtime/logs/notifications.jsonl`
    - `git rm commentary_02_srt2images_timeline/ui/src/runtime/logs/notifications.jsonl`
  - 判定: コード参照ゼロ（`git grep -n "notifications.jsonl"` がヒットしない）

### 2) SSOTの整理

- 削除: 旧 duplicate（ssot/completed/PLAN_STAGE1_PATH_SSOT_MIGRATION.md。`ssot/PLAN_STAGE1_PATH_SSOT_MIGRATION.md` と同内容の重複コピー）
  - 判定: diffゼロ（同一内容）を確認して削除

### 3) 退避（コピー）

- 退避コピー作成: `commentary_02_srt2images_timeline/` 直下のサンプル/残骸候補
  - 対象:
    - `commentary_02_srt2images_timeline/PROJ.json`
    - `commentary_02_srt2images_timeline/channel_preset.json`
    - `commentary_02_srt2images_timeline/persona.txt`
    - `commentary_02_srt2images_timeline/image_cues.json`
  - 先: `backups/20251212_repo_residue/commentary_02_legacy_root_artifacts/`
  - 注: この時点では **移動していない**（元ファイルは残置）

### 4) Gitignore（生成物ノイズ抑制）

- 追加:
  - `*.egg-info/`
  - `script_pipeline/data/CH*/**/audio_prep/`
  - `commentary_02_srt2images_timeline/**/runtime/logs/`

### 備考

- `__pycache__/` や `.pytest_cache/` は再生成されるため、必要に応じて随時削除する。

---

## 2025-12-13

### 1) `commentary_02` 直下の残骸（repo tracked）を削除

2025-12-12 にバックアップを作成済みのため、以下を **git rm**（削除）した。

- 削除:
  - `commentary_02_srt2images_timeline/PROJ.json`
  - `commentary_02_srt2images_timeline/channel_preset.json`
  - `commentary_02_srt2images_timeline/persona.txt`
  - `commentary_02_srt2images_timeline/image_cues.json`
- バックアップ（復元先）:
  - `backups/20251212_repo_residue/commentary_02_legacy_root_artifacts/`

### 2) バックアップファイル（.bak）の削除

意図: repo tracked のバックアップ残骸を除去し、探索ノイズを減らす。

- 削除:
  - `commentary_02_srt2images_timeline/tools/factory.py.bak`
  - `50_tools/projects/srtfile/srtfile_v2/tools/progress_manager.py.bak`
- 付随:
  - `.gitignore` に `*.bak` と `*~` を追加

### 3) 音声生成の残骸（巨大chunks等）の削除（untracked）

意図: final wav/srt/log は保持しつつ、再生成可能で容量最大の `chunks/` を削除して散らかりを減らす。

- 削除: `audio_tts_v2/artifacts/final/*/*/chunks/`（106件 / 約8.5GB）
  - 実行: `python3 scripts/purge_audio_final_chunks.py --run --keep-recent-minutes 60`

- 削除: `script_pipeline/data/*/*/audio_prep/chunks/`（3件 / 約127.4MB）
  - 実行: `python3 scripts/cleanup_audio_prep.py --run --keep-recent-minutes 60`

- 削除: `script_pipeline/data/*/*/audio_prep/{CH}-{NNN}.wav|.srt`（重複バイナリ 1件 / 約45.6MB）
  - 実行: `python3 scripts/purge_audio_prep_binaries.py --run --keep-recent-minutes 360`

### 4) Legacy隔離（repo tracked）

意図: 旧PoC/旧静的ビルド/メモを `legacy/` 配下へ隔離し、トップレベルを現行フローに集中させる。

- 移動（git mv）:
  - `50_tools/` → `legacy/50_tools/`
  - `docs/` → `legacy/docs_old/`
  - `idea/` → `legacy/idea/`
- 互換 symlink（repo tracked）:
  - `50_tools` → `legacy/50_tools`
  - `docs` → `legacy/docs_old`
  - `idea` → `legacy/idea`
- 証跡: commit `bad4051e`

### 5) Legacy隔離（repo tracked）

意図: 各ドメイン配下に残っている legacy 断片を `legacy/` に集約し、探索ノイズを削減する。

- 移動（git mv）:
  - `audio_tts_v2/legacy_archive/` → `legacy/audio_tts_v2/legacy_archive/`
  - `commentary_02_srt2images_timeline/tools/archive/` → `legacy/commentary_02_srt2images_timeline/tools/archive/`
- 互換 symlink（repo tracked）:
  - `audio_tts_v2/legacy_archive` → `../legacy/audio_tts_v2/legacy_archive`
  - `commentary_02_srt2images_timeline/tools/archive` → `../../../legacy/commentary_02_srt2images_timeline/tools/archive`
- 証跡: commit `0a4ed311`

## 2025-12-13

### 6) キャッシュ/不要メタの削除（untracked）

意図: 実行に不要で、探索ノイズと容量だけ増やすキャッシュを除去する（必要なら自動再生成される）。

- 削除:
  - `**/__pycache__/`（repo 配下のローカルキャッシュ。`.venv/` 等の依存環境は対象外）
  - `.pytest_cache/`
  - `**/.DS_Store`
- 実行: `rm -rf <dirs> && find . -name .DS_Store -delete`

### 7) `legacy/50_tools` の削除（repo tracked）

意図: 現行フローが参照しない旧PoC群を完全削除し、探索ノイズを恒久的に減らす。

- 事前対応（互換パスの撤去）:
  - `commentary_02_srt2images_timeline/tools/*` から `50_tools/50_1_capcut_api` の探索パスを削除（`CAPCUT_API_ROOT` / `~/capcut_api` / `packages/capcut_api` のみに統一）。
- アーカイブ（復元用）:
  - `backups/graveyard/20251213_122104_legacy_50_tools.tar.gz`
- 削除:
  - `legacy/50_tools/`
  - `50_tools`（互換symlink）

### 8) 破損 symlink の削除（untracked）

意図: 存在しない絶対パスへの symlink は事故の元なので除去する。

- 削除:
  - `credentials/srtfile-tts-credentials.json`（`/Users/dd/...` への破損リンク）

### 9) `legacy/docs_old` の削除（repo tracked）

意図: 旧静的ビルド（参照用）の残骸を削除し、現行の正本を `ssot/` に集約する。

- アーカイブ（復元用）:
  - `backups/graveyard/20251213_123223_legacy_docs_old.tar.gz`
- 削除:
  - `legacy/docs_old/`
  - `docs`（互換symlink）

### 10) legacyアーカイブの削除（repo tracked）

意図: 参照ゼロの過去版/退避を削除し、現行コード探索を軽くする。

- アーカイブ（復元用）:
  - `backups/graveyard/20251213_123409_legacy_archives.tar.gz`
- 削除:
  - `legacy/audio_tts_v2/legacy_archive/` + `audio_tts_v2/legacy_archive`（互換symlink）
  - `legacy/commentary_02_srt2images_timeline/tools/archive/` + `commentary_02_srt2images_timeline/tools/archive`（互換symlink）

### 11) キャッシュ掃除の再実行（untracked）

意図: 並列運用で増殖するキャッシュを都度落として、探索ノイズと容量を抑える。

- 実行: `bash scripts/ops/cleanup_caches.sh`

### 12) script_pipeline の古い中間ログ削除（untracked）

意図: script_pipeline の per-video logs / state logs が増殖するため、保持期限を超えたものを削除する。

- 削除（keep-days=14）:
  - `script_pipeline/data/_state/logs/*.log`（古い state logs）
  - `script_pipeline/data/*/*/logs/`（古い per-video logs）
- 実行: `python scripts/cleanup_data.py --run --keep-days 14`

### 13) `00_research` の workspaces 実体化（repo tracked）

意図: Stage2（workspaces抽出）の一環として、research を `workspaces/` 側へ寄せる（旧パスは互換symlink）。

- アーカイブ（復元用）:
  - `backups/graveyard/20251213_133243_00_research.tar.gz`
- 実行:
  - `rm workspaces/research`
  - `mv 00_research workspaces/research`
  - `ln -s workspaces/research 00_research`
- 結果:
  - `workspaces/research/` が正本
  - `00_research` は `workspaces/research` への symlink

### 14) `progress` の workspaces 実体化（repo tracked）

意図: Stage2（workspaces抽出）の一環として、planning SoT を `workspaces/` 側へ寄せる（旧パスは互換symlink）。

- アーカイブ（復元用）:
  - `backups/graveyard/20251213_133445_progress.tar.gz`
- 実行:
  - `rm workspaces/planning`
  - `mv progress workspaces/planning`
  - `ln -s workspaces/planning progress`
- 結果:
  - `workspaces/planning/` が正本
  - `progress` は `workspaces/planning` への symlink

### 15) Stage2: `workspaces/` cutover の確定（repo tracked）

意図: 生成物/中間生成物（台本・音声・動画・ログ）を repo から切り離し、`workspaces/` を正本に固定する。  
旧パスは互換 symlink として残し、参照側の破壊を防ぐ。

- アーカイブ（復元用）:
  - `backups/graveyard/20251213_181445_script_pipeline_data_HEAD.tar.gz`（`script_pipeline/data/**` の repo tracked 断面）
- 実行（git）:
  - `script_pipeline/data/**`（repo tracked の巨大データ）を index から削除し、`script_pipeline/data -> ../workspaces/scripts` の symlink を tracked 化
  - `workspaces/{audio,logs,scripts}` および `workspaces/video/{input,runs}` を tracked symlink から「実ディレクトリ + README/.gitignore」へ typechange
  - `workspaces/video/{input,runs}/.gitkeep` を追加（空ディレクトリでも存在を担保）
- 補足:
  - 生成物の保持/削除の基準は `ssot/PLAN_OPS_ARTIFACT_LIFECYCLE.md` に従う

### 16) `legacy/idea` の削除（repo tracked）

意図: 参照されない旧メモ/試作が残ると誤参照の原因になるため、アーカイブ後に削除して探索ノイズを恒久的に下げる。

- アーカイブ（復元用）:
  - `backups/graveyard/20251213_185921_legacy_idea.tar.gz`
- 削除:
  - `legacy/idea/`
  - `idea`（互換symlink）

### 17) `legacy/_old` の削除（untracked / local）

意図: repo 管理外の旧退避物（大量の古いspec/スクリプト/JSON）は、ローカル探索ノイズと誤実行リスクが高い。  
git の履歴には残らないため、**ローカルのみ**削除した。

- 削除:
  - `legacy/_old/`
  - `_old`（symlink）

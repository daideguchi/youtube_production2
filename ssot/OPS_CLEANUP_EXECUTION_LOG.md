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

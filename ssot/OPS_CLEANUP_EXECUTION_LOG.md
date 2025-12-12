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

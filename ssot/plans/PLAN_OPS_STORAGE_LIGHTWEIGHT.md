# PLAN_OPS_STORAGE_LIGHTWEIGHT — ディスク軽量化（生成物/ログ/キャッシュの定期整理）

## Plan metadata
- **Plan ID**: PLAN_OPS_STORAGE_LIGHTWEIGHT
- **ステータス**: Draft
- **担当/レビュー**: Owner: dd / Reviewer: dd
- **対象範囲 (In Scope)**: `workspaces/**`（audio/video/scripts/logs/thumbnails/tmp）、`log_research/`、ローカルキャッシュ（`__pycache__` 等）
- **非対象 (Out of Scope)**: git履歴の圧縮（filter-repo 等）、外部CapCutドラフトrootの削除方針（個別運用）
- **関連 SoT/依存**:
  - `ssot/ops/OPS_LOGGING_MAP.md`
  - `ssot/plans/PLAN_OPS_ARTIFACT_LIFECYCLE.md`
  - `ssot/plans/PLAN_UI_WORKSPACE_CLEANUP.md`
  - `python -m scripts.cleanup_workspace`
  - `python3 scripts/ops/cleanup_logs.py`
  - `python3 scripts/cleanup_data.py`
- **最終更新日**: 2026-01-08

## 1. 背景と目的
- 中間生成物（L2）と一時ログ/キャッシュ（L3）が溜まり、探索ノイズとディスク逼迫を起こす。
- SoT（正本）を絶対に守りつつ、**決定論 + dry-run 既定**で定期整理できる状態にする。

## 2. 成果物と成功条件 (Definition of Done)
- 量産運用者が迷わず実行できる「定期整理コマンド」と「削除対象の境界」が固定されている。
- `--dry-run` → `--run` の順で安全に運用でき、ログが `workspaces/logs/regression/**` に残る。
- SoT（`workspaces/scripts/**/status.json`, `workspaces/audio/final/**`, `workspaces/video/runs/**`, `workspaces/thumbnails/projects.json` など）を誤って消さない。

## 3. 方針（固定）
- 生成物分類（L0/L1/L2/L3）は `PLAN_OPS_ARTIFACT_LIFECYCLE.md` を正本とする。
- **削除は原則 workspaces（untracked）だけ**。repo tracked を消す場合は `PLAN_LEGACY_AND_TRASH_CLASSIFICATION.md` の条件 + archive-first（`backups/graveyard/`）を必須にする。
- 実行は必ず `--dry-run` を先に走らせ、問題が無ければ `--run`。

## 4. 推奨コマンド（定期）

### 4.1 日次（L3ログ/キャッシュ）
```bash
python -m scripts.cleanup_workspace --logs --dry-run
python -m scripts.cleanup_workspace --logs --run --logs-keep-days 30

# キャッシュ（untracked）は随時OK
bash scripts/ops/cleanup_caches.sh
```

### 4.2 週次（台本中間物/音声prep）
```bash
python -m scripts.cleanup_workspace --scripts --dry-run
python -m scripts.cleanup_workspace --scripts --run --scripts-keep-days 14
```

### 4.3 月次（video runs の整理）
```bash
python -m scripts.cleanup_workspace --video-runs --dry-run
# 運用の合意が取れてから:
python -m scripts.cleanup_workspace --video-runs --run --yes
```

## 5. 計測（肥大化の可視化）
```bash
python3 scripts/ops/logs_snapshot.py
du -sh workspaces/audio workspaces/video workspaces/scripts workspaces/logs 2>/dev/null | sort -h
```

## 6. リスクと対策
- **誤削除リスク**: dry-run 既定 + SoT境界を SSOT で固定 + archive-first（tracked）で回避。
- **並列衝突**: `scripts/agent_org.py lock` により作業範囲をロックしてから実行する。


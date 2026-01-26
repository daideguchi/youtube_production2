# PLAN_OPS_STORAGE_LIGHTWEIGHT — ディスク軽量化（生成物/ログ/キャッシュの定期整理）

## Plan metadata
- **Plan ID**: PLAN_OPS_STORAGE_LIGHTWEIGHT
- **ステータス**: Active
- **担当/レビュー**: Owner: dd / Reviewer: dd
- **対象範囲 (In Scope)**: `workspaces/**`（audio/video/scripts/logs/thumbnails/tmp）、`log_research/`、ローカルキャッシュ（`__pycache__` 等）
- **非対象 (Out of Scope)**: git履歴の圧縮（filter-repo 等）、外部CapCutドラフトrootの削除方針（個別運用）
- **関連 SoT/依存**:
  - `ssot/ops/OPS_LOGGING_MAP.md`
  - `ssot/plans/PLAN_OPS_ARTIFACT_LIFECYCLE.md`
  - `ssot/plans/PLAN_UI_WORKSPACE_CLEANUP.md`
  - `python3 scripts/ops/workspace_snapshot.py`
  - `python3 scripts/cleanup_workspace.py`
  - `python3 scripts/ops/cleanup_logs.py`
  - `python3 scripts/cleanup_data.py`
- **最終更新日**: 2026-01-26

## 1. 背景と目的
- 中間生成物（L2）と一時ログ/キャッシュ（L3）が溜まり、探索ノイズとディスク逼迫を起こす。
- SoT（正本）を絶対に守りつつ、**決定論 + dry-run 既定**で定期整理できる状態にする。

## 2. 成果物と成功条件 (Definition of Done)
- 量産運用者が迷わず実行できる「定期整理コマンド」と「削除対象の境界」が固定されている。
- `--dry-run` → `--run` の順で安全に運用でき、ログが `workspaces/logs/regression/**` に残る。
- SoT（`workspaces/scripts/**/status.json`, `workspaces/audio/final/**`, `workspaces/video/runs/**`, `workspaces/thumbnails/projects.json` など）を誤って消さない。

## 3. 方針（固定）
- 生成物分類（L0/L1/L2/L3）は `PLAN_OPS_ARTIFACT_LIFECYCLE.md` を正本とする。
- **削除対象は workspaces（untracked）だけ**。repo tracked を消す場合は `PLAN_LEGACY_AND_TRASH_CLASSIFICATION.md` の条件 + archive-first（`backups/graveyard/`）を必須にする。
- 実行は必ず `--dry-run` を先に走らせ、問題が無ければ `--run`。
- `workspaces/video/_archive/**` の **MOVE（退避）** は「探索ノイズ削減」には効くが、同一ディスク内では容量は減らない。容量削減が目的なら「削除」か「別ボリュームへ退避」が必要（削除は別途合意/ログを残して実施）。

## 4. 標準コマンド（定期）

### 4.1 日次（L3ログ/キャッシュ）
```bash
python3 scripts/cleanup_workspace.py --logs --dry-run
python3 scripts/cleanup_workspace.py --logs --run --logs-keep-days 30

# キャッシュ（untracked）は随時OK
bash scripts/ops/cleanup_caches.sh
```

### 4.2 週次（台本中間物/音声prep）
```bash
python3 scripts/cleanup_workspace.py --scripts --dry-run
python3 scripts/cleanup_workspace.py --scripts --run --scripts-keep-days 14
```

### 4.3 月次（video runs の整理）
```bash
# 全チャンネル対象（標準: まず dry-run）
python3 scripts/cleanup_workspace.py --video-runs --dry-run --all

# チャンネル指定で絞る（例）
python3 scripts/cleanup_workspace.py --video-runs --dry-run --channel CH04 --channel CH23

# 運用の合意が取れてから:
python3 scripts/cleanup_workspace.py --video-runs --run --all --yes
```

### 4.4 緊急（Disk STOP-level: 空き <= 30Gi）: audio_prep を先に削る

狙い:
- `workspaces/scripts/**/audio_prep/chunks/` と `audio_prep/{CH}-{NNN}*.wav/.srt` の重複バイナリを削り、数GB単位で空きを作る。
- SoT（`workspaces/audio/final/**`）は触らない。削除は rebuildable な chunk/重複のみ。

```bash
# まず dry-run（ログを残す）
python3 scripts/cleanup_workspace.py --audio --dry-run --channel CH01 --channel CH02 --channel CH04 --channel CH26

# OKなら run（同じscopeで実行）
python3 scripts/cleanup_workspace.py --audio --run --channel CH01 --channel CH02 --channel CH04 --channel CH26
```

## 5. 計測（肥大化の可視化）
```bash
# まずこれ（統一スナップショット。report も残す）
./ops snapshot workspace -- --write-report
# (= python3 scripts/ops/workspace_snapshot.py --write-report)

python3 scripts/ops/logs_snapshot.py
du -sh workspaces/audio workspaces/video workspaces/scripts workspaces/logs 2>/dev/null | sort -h
```

### 5.1 直近スナップショット（観測ベース）
2026-01-09（参考。環境/実行で変動する）:
- `workspaces/video`: 約 63G
- `workspaces/audio`: 約 18G
- `workspaces/scripts`: 約 12G
- `workspaces/thumbnails`: 約 6.5G
- `workspaces/logs`: 約 337M
- `workspaces/_scratch`: 約 238M
- `workspaces/tmp`: 約 44M

2026-01-26（Mac / Lenovo共有stub / 参考）:
- `workspaces/video`: 約 15G
- `workspaces/audio`: 約 5.2G
- `workspaces/scripts`: 約 18G（主因: `audio_prep/**`）
- `workspaces/thumbnails`: 約 1.8G
- `workspaces/logs`: 約 92M
- OSディスク空き（重要）: `/System/Volumes/Data` 空き約 `20Gi`（使用 `96%`）

### 5.2 Macの“workspaces外”で詰まりやすい場所（観測 / 2026-01-26）

重要: ここは **未投稿（Hot）を消す話ではない**。あくまで「バックアップ/アーカイブの二重化」で容量が詰まるケースの観測。

観測（read-only）:
- `/Users/dd/mounts/_archive/2026-01-26` が約 `73G`（mountpointの退避/検証の残骸が集積）
  - `capcut_projects_backup_20260125T233620Z` 約 `26G`（CapCutドラフトroot `com.lveditor.draft` の“バックアップコピー”）
    - 実体（Hot側）: `/Users/dd/Movies/CapCut/User Data/Projects/com.lveditor.draft`（約 `30G`）
    - ※ ここを消しても **CapCutの現行ドラフト本体は消えない**（バックアップが減るだけ）
  - `lenovo_share_real__LOCAL_BACKUP__20260125T230525Z` 約 `44G`（共有/Outbox/ytm_workspaces のローカル退避）
    - 共有が不安定な間は“保険”の可能性があるため、削除は要注意（まず共有の健全性を確認）
  - `capcut_decrypted_images_*`（outbox/_snapshots）: 数GB（CapCut cloudDraft DB から復元できる派生物）

対応方針（固定 / safe order）:
1) まず UI の `<UI_BASE>/storage-status` で **disk free / 共有の健全性（uploads/ytm_workspaces の有無）** を確認する
2) 「共有が壊れている/空」の間は、`LOCAL_BACKUP` を先に消さない（事故時の復元に必要な可能性がある）
3) 容量が厳しい場合は、まず **再生成可能な派生物**（例: `capcut_decrypted_images_*`）から整理する
4) `capcut_projects_backup_*` は「バックアップを残すか」の意思決定が必要（削除は *Hot本体ではない* が、保険を減らす）
   - 実行は必ず「dry-run（候補とサイズ提示）→承認→run」でログを残す

注:
- `./ops snapshot workspace -- --write-report` の出力に `report=...json` が出るので、数値は常にその時点の観測を正とする。
- 環境によっては `workspaces/video` / `workspaces/scripts` が 0B のこともある（別worktree/別ディスクを使っている等）。

優先順位（迷わない順）:
1) **キャッシュ/ログ（低リスク）**: `bash scripts/ops/cleanup_caches.sh` / `python -m scripts.cleanup_workspace --logs --dry-run` → `--run`
2) **台本中間物（L2/L3）**: `python -m scripts.cleanup_workspace --scripts --dry-run` → `--run`
3) **video runs（最も肥大化しやすい）**: `python -m scripts.cleanup_workspace --video-runs --dry-run ...` → 合意後に `--run --yes`
4) **audio final chunks（published/ready後）**: `python3 scripts/purge_audio_final_chunks.py --help`

## 6. リスクと対策
- **誤削除リスク**: dry-run 既定 + SoT境界を SSOT で固定 + archive-first（tracked）で回避。
- **並列衝突**: `scripts/agent_org.py lock` により作業範囲をロックしてから実行する。

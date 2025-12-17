# OPS_VIDEO_RUNS_ARCHIVE_RESTORE — Video runs のアーカイブ/復旧（依存・参照の正本）

目的:
- `workspaces/video/runs/` が巨大化すると、探索・UI・運用が破綻する。
- ただし **CapCut や運用ツールの参照が壊れる可能性がある**ため、必ず「戻せる」前提で整理する。

この文書は **依存/参照関係 + アーカイブ/復旧の正しいやり方**を 1 枚にまとめた SSOT。

関連:
- 確定フロー: `ssot/OPS_CONFIRMED_PIPELINE_FLOW.md`（Phase D: Video）
- 生成物ライフサイクル: `ssot/PLAN_OPS_ARTIFACT_LIFECYCLE.md`
- cleanup 実行ログ: `ssot/OPS_CLEANUP_EXECUTION_LOG.md`
- run 選択: `scripts/episode_ssot.py`（`metadata.video_run_id` の採用）

---

## 1) SoT（正本）とディレクトリ

- Active run（正本）: `workspaces/video/runs/<run_id>/`
- Archive（退避）: `workspaces/video/_archive/<timestamp>/.../runs/<run_id>/`
  - **削除しない**（まず退避して混乱を減らす）
  - 退避の差分は `archive_report.json` と `OPS_CLEANUP_EXECUTION_LOG.md` に残す

---

## 2) 依存/参照関係（壊れる可能性のあるもの）

### 2.1 Script SoT からの参照（重要）
- `workspaces/scripts/<CH>/<NNN>/status.json: metadata.video_run_id`
  - ここに `run_id` が入っている場合、その episode の採用 run（1:1）として扱う。
  - run を退避してしまうと、UI/ツールが `runs/<run_id>` を前提にしている場合に見失う可能性がある。

### 2.2 Run 内の CapCut link（壊れにくいが要注意）
- `workspaces/video/runs/<run_id>/capcut_draft`（symlink）
  - これは **外部の CapCut project dir へのリンク**。
  - run dir を `_archive/` に移動しても symlink 自体は付いていく（CapCut 側が run を参照しているわけではない）。
  - ただし「運用ツールが run dir を探せない」問題は起き得る（復旧で対応）。

### 2.3 UI / ops の参照
- UI の VideoProduction は `video_runs_root()` を使って run を列挙/参照する。
- ジョブや検証ログは `workspaces/logs/ui_hub/video_production/`（L3）。

---

## 3) 安全ガード（必須）

- 既定は dry-run（差分だけ見る）
- `keep-recent-minutes` 以内に更新された run は触らない（生成中/作業中を避ける）
- `.keep` マーカーがある run は触らない（人間が明示保護）
- episode の run は `keep-last-runs` を最低 2（デフォルト）残す

---

## 4) アーカイブ（退避）手順

### 4.1 ふつう（CH/動画を指定して安全に）
```
python3 scripts/cleanup_workspace.py --video-runs --dry-run --channel CHxx --video NNN
python3 scripts/cleanup_workspace.py --video-runs --run --channel CHxx --video NNN
```

### 4.2 unscoped/legacy run をまとめて退避（まず “戻せる” 前提で）
> 数字run（`192` 等）、`api_*`、`jinsei*`、`CHxx-` など、episode と 1:1 で紐付いていない run が対象。

```
python3 scripts/cleanup_workspace.py --video-runs --all --dry-run \
  --video-unscoped-only \
  --video-archive-unscoped \
  --video-archive-unscoped-legacy \
  --keep-recent-minutes 1440

python3 scripts/cleanup_workspace.py --video-runs --all --run --yes \
  --video-unscoped-only \
  --video-archive-unscoped \
  --video-archive-unscoped-legacy \
  --keep-recent-minutes 1440
```

出力:
- run 実行時: `workspaces/video/_archive/<timestamp>/archive_report.json`
- dry-run 時: `workspaces/logs/regression/video_runs_cleanup_dryrun_<timestamp>.json`

---

## 5) 復旧（戻す）手順

`archive_report.json` から **確実に元の場所へ戻す**:
```
python3 scripts/ops/restore_video_runs.py --report /path/to/archive_report.json
python3 scripts/ops/restore_video_runs.py --report /path/to/archive_report.json --run
```

部分復旧（run_id 指定）:
```
python3 scripts/ops/restore_video_runs.py --report /path/to/archive_report.json --only-run-id <run_id> --run
```

復旧レポート:
- dry-run: `workspaces/logs/regression/restore_video_runs_dryrun_<timestamp>.json`
- run: `archive_report.json` の隣に `restore_report_<timestamp>.json`

---

## 6) “diff（証跡）” の正本

- 退避の内容（何をどこへ移動したか）: `archive_report.json`
- 退避の実行記録（なぜ/どう実行したか）: `ssot/OPS_CLEANUP_EXECUTION_LOG.md`
- 差分確認: `git diff ssot/OPS_CLEANUP_EXECUTION_LOG.md`


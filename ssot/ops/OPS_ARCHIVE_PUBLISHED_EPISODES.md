# OPS_ARCHIVE_PUBLISHED_EPISODES — 投稿済み（進捗=投稿済み）成果物のアーカイブ運用

目的:
- 「投稿済み」の成果物を **探索ノイズ/容量の観点でアーカイブ（移動）または削除**し、作業対象（未投稿/推敲中）だけが前面に残る状態にする。
- ただし **実績（SoT/監査情報）は残す**（archive/delete いずれでもレポートを残す）。

正本（SoT）:
- 公開済み判定: `workspaces/planning/channels/CHxx.csv` の `進捗=投稿済み`
  - 追加ガードが必要な場合は `status.json: metadata.published_lock=true` を併用する（ただし一次判定はPlanning）。

実行スクリプト（入口）:
- `python3 scripts/ops/archive_published_episodes.py --help`

標準運用（容量対策; 投稿済み）:
- 音声（`workspaces/audio/final/**`）と CapCut run_dir（`workspaces/video/runs/**`）は **削除**する（台本/進捗/監査ログは保持）。
- dry-run:
  - `./scripts/with_ytm_env.sh python3 scripts/ops/archive_published_episodes.py --channel CHxx --audio --video-runs --delete --dry-run`
- 実行:
  - `./scripts/with_ytm_env.sh python3 scripts/ops/archive_published_episodes.py --channel CHxx --audio --video-runs --delete --run --yes`

## 1. 何をアーカイブするか（ドメイン別）

アーカイブ対象は「公開済み＝リテイクしない前提」を前提に、下記の “大きい/探索ノイズになりやすい” 生成物を中心にする。

- audio:
  - `workspaces/audio/final/<CH>/<NNN>/`（wav/srt/log.json 等）
  - 退避先: `workspaces/audio/_archive_audio/<timestamp>/<CH>/<NNN>/`
- thumbnails:
  - `workspaces/thumbnails/assets/<CH>/<NNN>*`（例: `216`, `216_2`）
  - 退避先: `workspaces/thumbnails/_archive/<timestamp>/<CH>/assets/<NNN>*/`
- video input:
  - `workspaces/video/input/<CH>_*/` 配下の、当該エピソードに紐づくファイル（`CHxx-NNN.*` や `NNN.*` 等）
  - 退避先: `workspaces/video/_archive/<timestamp>/<CH>/video_input/<CH>_*/...`
- video runs:
  - `workspaces/video/runs/<run_id>/` のうち、`timeline_manifest` 等から当該エピソードに紐づくもの
  - 退避先: `workspaces/video/_archive/<timestamp>/<CH>/runs/<run_id>/`

固定ルール:
- 標準（容量対策; 投稿済み）: `--delete --audio --video-runs` を使う（台本/進捗は保持）。
- `--delete` 無し: **移動＝アーカイブ**（復元用途）。
- `--delete` 有り: **削除**。
- 移動/削除は “既存パスが消える” ため、復元する場合は restore スクリプトを追加する。

## 2. セーフティ（必須）

- 既定は dry-run（実行には `--run` が必要）。
- `--run` で複数件を処理する場合は `--yes` を必須にする（事故防止）。
- `--delete` を使う場合は **対象ドメイン（`--audio/--video-input/--video-runs/--thumbnails`）を明示**し、`--delete --run` には `--yes` を必須にする（事故防止）。
- `scripts/agent_org.py lock` のロックを尊重し、該当スコープがロック中ならスキップして report に理由を残す。

## 3. ログ/レポート

- report: `workspaces/logs/regression/archive_published_episodes/`
- JSONに “何をどこへ移したか/なぜスキップしたか” を残す（実績保持）。

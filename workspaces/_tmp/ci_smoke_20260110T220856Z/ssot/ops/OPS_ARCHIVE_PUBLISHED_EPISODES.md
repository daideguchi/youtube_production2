# OPS_ARCHIVE_PUBLISHED_EPISODES — 投稿済み（進捗=投稿済み）成果物のアーカイブ運用

目的:
- 「投稿済み」の成果物を **探索ノイズ/容量の観点でアーカイブ**し、作業対象（未投稿/推敲中）だけが前面に残る状態にする。
- ただし **実績（SoT/監査情報）は残す**（削除ではなく移動＋レポート）。

正本（SoT）:
- 公開済み判定: `workspaces/planning/channels/CHxx.csv` の `進捗=投稿済み`
  - 追加ガードが必要な場合は `status.json: metadata.published_lock=true` を併用する（ただし一次判定はPlanning）。

実行スクリプト（入口）:
- `python3 scripts/ops/archive_published_episodes.py --help`

## 1. 何をアーカイブするか（ドメイン別）

アーカイブ対象は「公開済み＝原則リテイクしない」を前提に、下記の “大きい/探索ノイズになりやすい” 生成物を中心にする。

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

原則:
- **削除はしない**（移動＝アーカイブ）。
- 移動は “既存パスが消える” ため、運用上必要なら restore スクリプト（将来）を追加する。

## 2. セーフティ（必須）

- 既定は dry-run（実行には `--run` が必要）。
- `--run` で複数件を処理する場合は `--yes` を必須にする（事故防止）。
- `scripts/agent_org.py lock` のロックを尊重し、該当スコープがロック中ならスキップして report に理由を残す。

## 3. ログ/レポート

- report: `workspaces/logs/regression/archive_published_episodes/`
- JSONに “何をどこへ移したか/なぜスキップしたか” を残す（実績保持）。


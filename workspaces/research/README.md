# workspaces/research

ジャンル別の **ベンチマーク台本 / 構造分析 / 参考メモ** を置く作業場です。

- 入口: `workspaces/research/INDEX.md`（ジャンル索引）
- チャンネル側 SoT: `packages/script_pipeline/channels/CHxx-*/channel_info.json` の `benchmarks.script_samples` がここを参照します（UI `/benchmarks` でも確認可）。
- 迷ったら一旦: `workspaces/research/INBOX/`（未整理→後でジャンルへ仕分け）
- INDEX更新: `python3 scripts/ops/research_genre_index.py --apply`（`INDEX.md` の手動メモは `<!-- MANUAL START/END -->` 内に書く）

- 環境変数は必ずリポジトリ直下の `.env`（SSOT: `ssot/ops/OPS_ENV_VARS.md`）を利用します。このディレクトリに `.env` を置くことは禁止です。
- スクリプトの結果やレポートは `workspaces/logs/` や `workspaces/planning/` に集約します（ここは参照用の資料置き場）。
- `workspaces/research/_local/` はローカル専用（git 対象外）です。

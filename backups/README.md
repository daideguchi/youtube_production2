# backups/ — archive-first 退避（graveyard）

このフォルダは **運用上の safety net** です。

- 正本: archive-first の退避先は `backups/graveyard/`（`manifest.tsv` + 退避コピー）
- 証跡: `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md` が「何を/なぜ/どう退避して削除したか」の正本
- 注意: パイプラインの SoT（入力/出力/生成物）は `workspaces/**`。`backups/**` を SoT にしない

外部SSDへの offload は **標準運用に含めません**（安定性/再現性の観点で避ける）。

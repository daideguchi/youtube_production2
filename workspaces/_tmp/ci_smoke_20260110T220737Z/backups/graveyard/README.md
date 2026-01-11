# backups/graveyard/ — archive-first の退避ペイロード

ここは **「削除前に退避したコピー」** を置く場所です。現行フローの正本ではありません。

- 正本（運用/実行/参照入口）は `ssot/` と `workspaces/` と `packages/` / `apps/`。
- graveyard 配下には **廃止済みの旧名/旧構成** が含まれることがあります（復旧用の履歴として保持）。
- 探索ノイズ削減のため、通常の `rg` 検索からは除外しています（`.rgignore`）。

証跡（何を/なぜ/どう退避して削除したか）は `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md` が正本です。


# ui/tools - UI 専用ツール置き場

最終更新: 2025-11-10 / 担当: Codex

このディレクトリは UI スタック（FastAPI + React）を運用するための CLI/スクリプトを集約する場所です。以下の順で整備を進めます。

## 予定されているツール
| ファイル | 役割 | 状態 |
| --- | --- | --- |
| `start_manager.py` | UI の start/stop/status/restart、ログ閲覧、ポート開放 | ✅ 実装済 (Step 2) |
| `logviewer.py` | backend/frontend ログのフィルタ・grep | ✅ 実装済 (Step 3) |
| `port_guard.py` | ポート監視 / プロセス確認 | ✅ 実装済 (Step 3) |
| `build_check.py` | npm/pip/pytest スモークテスト | ✅ 実装済 (Step 4) |
| `health_probe.py` | FastAPI/React ヘルスチェック | ✅ 実装済 (Step 4) |
| `assets_sync.py` | thumbnails/assets/data 同期補助 | ✅ 実装済 (Step 5) |

詳細な計画は `docs/ui/TOOLS_PLAN.md` を参照してください。ツールの実装が完了したら README も随時更新します。

## ツールメモ

### start_manager.py

UI スタックの起動制御と補助ツールを提供するエントリポイントです。

```
cd <REPO_ROOT>
./start.sh start
python -m ui.tools.start_manager status
python -m ui.tools.start_manager restart --force
python -m ui.tools.start_manager logs --component backend --lines 120
python -m ui.tools.start_manager healthcheck --with-guards
```

- `start` / `stop` / `status` … 従来通り。`start` と `restart` は `.env` をバリデーションしてから backend/frontend を順に起動。
- `restart` … `stop` → `start` を自動実行（`--force` で SIGKILL 停止）。
- `logs` … `logs/ui_hub/backend.log` / `frontend.log` を tail 表示。`--component all` で両方を一括出力。
- `healthcheck` … ポート 8000/3000 への TCP 接続を確認し、稼働状態を色付きで表示。`--with-guards` で API/validate/prompt 等のガードを追加実行。

### assets_sync.py

`thumbnails/assets/{CH}/{video}` の階層を planning SoT（`workspaces/planning/channels/*.csv`。互換: `progress/`）と同期する補助 CLI です。

```
# CH01 だけ dry-run で確認
python -m ui.tools.assets_sync ensure --channels CH01 --dry-run

# 全チャンネルの不足ディレクトリを作成し meta.json を書き出す
python -m ui.tools.assets_sync ensure --refresh-meta

# 不整合のみレポート（CI で使用可）
python -m ui.tools.assets_sync report --fail-on-issues
```

- `ensure` : 企画ごとのフォルダを作成し、`meta.json` にタイトル/作成フラグ/進捗を記録。
- `report` : planning.csv と実ディレクトリの差分（欠落・孤立）を表示。`--fail-on-issues` を付けると不整合で終了コード1。
- `--channels`, `--videos`, `--include-flags`, `--exclude-flags` で対象を絞り込めます。

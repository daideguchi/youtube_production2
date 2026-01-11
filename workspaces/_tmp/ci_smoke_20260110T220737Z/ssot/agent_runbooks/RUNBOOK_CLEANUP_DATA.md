# RUNBOOK_CLEANUP_DATA — `workspaces/scripts` の中間物/ログ整理

## Runbook metadata
- **Runbook ID**: RUNBOOK_CLEANUP_DATA
- **ステータス**: Active
- **対象**: 台本パイプラインの中間物/ログ（安全に削除できる範囲のみ）
- **最終更新日**: 2025-12-22

## 1. 目的（DoD）
- `scripts/cleanup_data.py` を **dry-run** で確認できる
- 必要時のみ `--run` で削除し、並列衝突（coordination lock）を避ける

## 2. 実行（dry-run → run）
```bash
python3 scripts/cleanup_data.py
python3 scripts/cleanup_data.py --run
```

keep-days を変えたい場合:
```bash
python3 scripts/cleanup_data.py --keep-days 30
python3 scripts/cleanup_data.py --keep-days 30 --run
```

## 3. 何が消える/残る（I/O）
削除対象（例: `--keep-days 14` のとき）:
- `workspaces/scripts/_state/logs/*.log` のうち14日より古いもの
- 各 `workspaces/scripts/CHxx/NNN/` 配下の `logs/`（14日より古いもの）
- 各 `workspaces/scripts/CHxx/NNN/` 配下の `audio_prep/`（14日より古く、かつ `workspaces/audio/final/CHxx/NNN/CHxx-NNN.{wav,srt}` が揃っているもの）

残すもの:
- `content/`（台本テキスト）、`output/`（最終原文/台本）、`status.json` など SoT は削除しない

## 4. 注意（lock尊重）
安全のため、default は dry-run（削除しない）。実際に削除する場合は `--run` を付ける。  
また、並列作業の衝突防止のため coordination lock を尊重する（危険なので通常 `--ignore-locks` は使わない）。

## 5. 定期実行（任意）
cron例（週1回、日曜3時 / `--run` を明示）:
```bash
0 3 * * 0 cd <REPO_ROOT> && PYTHONPATH="<REPO_ROOT>:<REPO_ROOT>/packages" /usr/bin/python3 scripts/cleanup_data.py --run >> workspaces/logs/cleanup_data.log 2>&1
```

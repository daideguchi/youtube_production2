# cleanup_data.py の使い方

```
python scripts/cleanup_data.py
```

- 削除対象:
  - `script_pipeline/data/_state/logs/*.log` のうち14日より古いもの
  - 各CHxx/NNN配下の `audio_prep` と `logs` ディレクトリ（いずれも14日より古いもの）
- 残すもの:
  - `content/`（台本テキスト）、`output/`（最終原文/台本）、`status.json` 等は削除しない

安全のため、default は dry-run（削除しない）。実際に削除する場合は `--run` を付ける。

```
python scripts/cleanup_data.py --run
```

cron例（週1回、日曜3時 / `--run` を明示）:
```
0 3 * * 0 cd /Users/dd/10_YouTube_Automation/factory_commentary && /usr/bin/python3 scripts/cleanup_data.py --run >> logs/cleanup.log 2>&1
```

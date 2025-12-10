# cleanup_data.py の使い方

```
cd /Users/dd/10_YouTube_Automation/factory_commentary
python scripts/cleanup_data.py
```

- 削除対象:
  - `script_pipeline/data/_state/logs/*.log` のうち14日より古いもの
  - 各CHxx/NNN配下の `audio_prep` と `logs` ディレクトリ
- 残すもの:
  - `content/`（台本テキスト）、`output/`（最終原文/台本）、`status.json` 等は削除しない

cron例（週1回、日曜3時）:
```
0 3 * * 0 cd /Users/dd/10_YouTube_Automation/factory_commentary && /usr/bin/python3 scripts/cleanup_data.py >> logs/cleanup.log 2>&1
```

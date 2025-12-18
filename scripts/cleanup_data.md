# cleanup_data.py の使い方

```
python scripts/cleanup_data.py
```

- 削除対象:
  - `workspaces/scripts/_state/logs/*.log` のうち14日より古いもの
  - 各 `workspaces/scripts/CHxx/NNN/` 配下の `logs` ディレクトリ（14日より古いもの）
  - 各 `workspaces/scripts/CHxx/NNN/` 配下の `audio_prep` ディレクトリ（14日より古く、かつ `workspaces/audio/final/CHxx/NNN/CHxx-NNN.{wav,srt}` が揃っているもの）
- 残すもの:
  - `content/`（台本テキスト）、`output/`（最終原文/台本）、`status.json` 等は削除しない

安全のため、default は dry-run（削除しない）。実際に削除する場合は `--run` を付ける。
また、並列作業の衝突防止のため coordination lock を尊重する（危険なので通常 `--ignore-locks` は使わない）。

```
python scripts/cleanup_data.py --run
```

cron例（週1回、日曜3時 / `--run` を明示）:
```
0 3 * * 0 cd <REPO_ROOT> && /usr/bin/python3 scripts/cleanup_data.py --run >> logs/cleanup.log 2>&1
```

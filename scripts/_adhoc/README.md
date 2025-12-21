# scripts/_adhoc — 一時スクリプト置き場（P3 / 非正規）

目的:
- その場限りの検証/一時バッチを **主線（P0/P1）に混ぜない**。
- 低知能エージェントが `scripts/` を探索しても迷わない状態を保つ。

ルール:
- 原則このディレクトリは `.gitignore` で除外（コミットしない）。
- 1ファイルごとに必ずメタ情報を付ける（owner/created/expires）。
- 期限が来たら削除する。必要になったら **正規入口（scripts/ または scripts/ops/）へ昇格**し、
  `ssot/OPS_ENTRYPOINTS_INDEX.md` と `ssot/OPS_SCRIPTS_PHASE_CLASSIFICATION.md` を更新する。

テンプレ:
```python
#!/usr/bin/env python3
"""
adhoc: <目的>
owner: <agent/person>
created: YYYY-MM-DD
expires: YYYY-MM-DD
notes: <消し忘れ防止の一言>
"""
```


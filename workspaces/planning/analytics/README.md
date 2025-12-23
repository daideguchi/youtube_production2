# Analytics SoT
- SoT: `progress/analytics/<channel>.csv` （Day1/7/30 指標を記録）
- ログ: 取得ログ `logs/analytics/fetch_YYYYMMDD.log`、改善ログ `logs/analytics/actions_YYYYMMDD.md`
- 対応要件: REQ-P4-001〜003

## 推奨カラム
| 日付 | チャンネル | 動画ID | 動画タイトル | 期間 | 再生数 | CTR | 平均視聴 % | 視聴時間 (h) | いいね | コメント | 共有 | メモ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2025-11-17 | CH06 | CH06-001 | 都市伝説XX | Day1 | 1234 | 7.1 | 62.0 | 128 | 45 | 12 | 3 | β取得 |

## 運用
1. KPI 取得時に CSV へ追記し、`logs/analytics/fetch_*.log` に取得コマンドや API レスポンス要約を残す。
2. 改善アクションは `logs/analytics/actions_*.md` に記録し、HISTORY に `[REQ-P4-00x]` でリンク。
3. UI/API で返す JSON はこのカラム構成に揃える（現行の対応表は `ssot/ops/DATA_LAYOUT.md` を参照）。

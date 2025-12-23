# OPS_CHANNEL_BENCHMARKS — チャンネル別ベンチマーク管理（SoT）

目的:
- **全チャンネルの「ベンチマークチャンネル情報 / 台本サンプル / 参考構成メモ」**を、迷わず参照できる形で一元管理する。
- 企画（Planning）・台本（Script）・サムネ/タイトルの“勝ちパターン”を、UI からすぐ確認できる状態にする。

関連:
- 新規チャンネル追加: `ssot/OPS_CHANNEL_LAUNCH_MANUAL.md`
- 参照/編集する正本: `ssot/OPS_SCRIPT_SOURCE_MAP.md`

---

## 1. SoT（正本）

**チャンネル別ベンチマークの正本は `channel_info.json` の `benchmarks` フィールド**。

- SoT: `packages/script_pipeline/channels/CHxx-*/channel_info.json`
- 集約（読み取り用）: `packages/script_pipeline/channels/channels_info.json`
  - ※集約は UI が読む“カタログ”。編集は必ず `CHxx-*/channel_info.json` 側で行い、必要なら再生成する。

---

## 2. `benchmarks` スキーマ（v1）

`benchmarks` は「競合チャンネル」と「台本サンプル」を最小セットとして持つ。

```json
{
  "benchmarks": {
    "version": 1,
    "updated_at": "2025-12-23",
    "channels": [
      {
        "handle": "@example",
        "name": "チャンネル名（任意）",
        "url": "https://www.youtube.com/@example",
        "note": "何を学ぶか（任意）"
      }
    ],
    "script_samples": [
      {
        "base": "research",
        "path": "benchmarks_ch07_ch08.md",
        "label": "ベンチマークメモ（任意）",
        "note": "使いどころ（任意）"
      }
    ],
    "notes": "総評（任意）"
  }
}
```

### 2.1 `script_samples` のパス規約（UI プレビュー対応）
- `base`: `"research"` または `"scripts"`
- `path`: `workspaces/{base}/` 配下の相対パス
  - 例: `base="research", path="ブッダ系/バズ台本構造分析.md"`
  - 例: `base="scripts", path="CH10/002/content/assembled.md"`

---

## 3. 運用ルール（事故防止）

- **最低限の要件**
  - `benchmarks.channels` に 1 件以上（競合/参考チャンネル）。`handle`（推奨）または `url` のどちらかは必須。
  - `benchmarks.script_samples` に 1 件以上（台本サンプル）。
- **迷ったら**
  - まず `workspaces/research/` 内の該当ジャンル資料を `script_samples` に紐付ける（後から競合URLを追加してよい）。
- **「見つけたが未確定」を放置しない**
  - 未確定事項は `note` に「未確定」と書き、UI の監査（audit）で検出できる状態にする。

---

## 4. UI での確認

- `/channel-settings` の「ベンチマーク」パネルで、チャンネルごとの競合/台本サンプルを確認・更新する。
- `/channel-settings` の「監査（全チャンネル）」で、ハンドル/タグ/説明文/ベンチマークの欠損を横断確認する。

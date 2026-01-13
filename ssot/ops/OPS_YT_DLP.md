# OPS_YT_DLP — yt-dlp運用（競合タイトル/メタ収集）SSOT

目的:
- 競合（ベンチマーク）チャンネルの **直近タイトル傾向** を素早く収集し、企画CSVのタイトル/コピーを「勝ち筋」に寄せる。
- DLせず **公開メタデータのみ** を扱い、`workspaces/research/` に再利用可能な形で整理する。

前提:
- `yt-dlp` がインストールされていること（例: `yt-dlp --version`）。

---

## 1) 入力SoT（どの競合を取るか）

- 競合定義は各チャンネルの `benchmarks.channels`（SoT）:
  - `packages/script_pipeline/channels/CHxx-*/channel_info.json`
  - 例: `packages/script_pipeline/channels/CH01-人生の道標/channel_info.json`

---

## 2) 出力（ログ/レポートの置き場）

- 生ログ（L3: Ephemeral / 再解析用）:
  - `workspaces/logs/ops/yt_dlp/flat__<playlist_channel_id>__YYYYMMDDTHHMMSSZ.jsonl`
- 整理レポート（research: 引用/再利用用）:
  - `workspaces/research/YouTubeベンチマーク（yt-dlp）/<playlist_channel_id>/report.md`
  - `workspaces/research/YouTubeベンチマーク（yt-dlp）/<playlist_channel_id>/report.json`
  - `workspaces/research/YouTubeベンチマーク（yt-dlp）/REPORTS.md`（集約）

---

## 3) “タイトルだけ”を最速で取る（標準）

```bash
# @HANDLE の直近80本の「動画ID + タイトル」だけ取得（DLしない）
mkdir -p workspaces/logs/ops/yt_dlp
yt-dlp --flat-playlist --playlist-end 80 --extractor-args "youtube:lang=ja" \
  --print "%(id)s\t%(title)s" "https://www.youtube.com/@HANDLE/videos" \
  > "workspaces/logs/ops/yt_dlp/titles__HANDLE__$(date +%Y%m%d).tsv"
```

事故防止:
- **`--flat-playlist` を使う**（重い抽出やDLに寄せない）
- 文字化け/翻訳表示が出る場合は `youtube:lang=ja` を明示する

---

## 4) “全部（ベンチマーク一括）”を整理レポート化する（標準）

ベンチマーク定義（`benchmarks.channels`）を入力にして、タイトル/尺/再生数/サムネURLを集計する。

```bash
# 全チャンネルの benchmarks.channels（重複除去）を一括で更新
python3 scripts/ops/yt_dlp_benchmark_analyze.py --all --apply
```

チャンネル単位（`CHxx` の `benchmarks.channels` のみ）:
```bash
python3 scripts/ops/yt_dlp_benchmark_analyze.py --channel CHxx --apply
```

単体（1競合）:
```bash
python3 scripts/ops/yt_dlp_benchmark_analyze.py --url "https://www.youtube.com/@HANDLE" --apply
```

確認先:
- `workspaces/research/YouTubeベンチマーク（yt-dlp）/REPORTS.md`
- `workspaces/research/YouTubeベンチマーク（yt-dlp）/<playlist_channel_id>/report.md`

---

## 5) よくある落とし穴

- 競合チャンネルの CTR は取得できない（Analyticsの所有者指標）。
- 投稿日/いいね/コメント等が必要な場合は、まず `report.md` で「当たり候補」を絞ってから深掘りする（全件深掘りは重い）。
- `--skip-download` などを混ぜて複雑化しない（まずは `--flat-playlist` で十分）。

# DATA_LAYOUT — 現行データ格納構造の実態

## 1. 概要

- このドキュメントは、現在の youtube 制作パイプラインで
  「実際に git にコミットされているデータの格納場所」を記述する。
- 理想案ではなく、**2025-01-10 時点の事実**を優先する。

## 2. チャンネル別・動画別の生成データ

### 2.1 script_pipeline/data

- ルート: `script_pipeline/data/CH{NN}/{VIDEO_NO}/`
- 代表例:

#### CH07-025 の例

```text
script_pipeline/data/CH07/025
├── audio_prep
│   ├── script_sanitized_with_pauses.txt
│   └── script_sanitized.txt
├── content
│   └── assembled.md
└── status.json

3 directories, 4 files
```

#### CH05-029 の例

```text
script_pipeline/data/CH05/029
├── audio_prep
│   ├── a_text.txt
│   ├── annotations.json
│   ├── audio_meta.json
│   ├── b_text_build_log.json
│   ├── b_text_with_pauses.txt
│   ├── b_text.txt
│   ├── CH05-029.srt
│   ├── CH05-029.wav
│   ├── chunks
│   │   ├── CH05-029_block_000.wav
│   │   ├── CH05-029_block_001.wav
│   │   ...
│   ├── engine_metadata.json
│   ├── inference_metadata.txt
│   ├── kana_engine.json
│   ├── log.json
│   ├── pause_map.json
│   ├── srt_blocks.json
│   ├── srt_entries.json
│   └── tokens.json
├── content
│   └── assembled.md
└── status.json

4 directories, 364 files
```

### 2.2 audio_tts_v2/artifacts

* ルート: `audio_tts_v2/artifacts/final/CH{NN}/{VIDEO_NO}/`

#### 例: CH09-024 の構造

```text
audio_tts_v2/artifacts/final/CH09/024
└── a_text.txt

1 directory, 1 file
```

### 2.3 commentary_02_srt2images_timeline

* ルート: `commentary_02_srt2images_timeline/...`
* 役割: SRT → 画像タイムライン生成
* 該当ディレクトリはコードのみ、生成結果は他ディレクトリに配置

### 2.4 progress/channels

* ルート: `progress/channels/`

実在するファイル一覧（例）:

```text
CH01.csv
CH02.csv
CH03.csv
CH04.csv
CH05.csv
CH06.csv
CH07.csv
CH08.csv
CH09.csv
CH10.csv
CH11.csv
```

CH01.csvのカラム例:
- No.,タイトル,進捗,ScriptPolish,ScriptDraft,ScriptOutline,ScriptReview,QualityCheck,ChapterBrief,AudioSubtitle

### 2.5 thumbnails

* ルート: `thumbnails/`

代表例として、CH01の画像ディレクトリ:

```text
thumbnails/CH01_人生の道標/192/2.png
thumbnails/CH01_人生の道標/192/1.png
thumbnails/CH01_人生の道標/192.zip
thumbnails/CH01_人生の道標/ch01_207 (2)/2.png
thumbnails/CH01_人生の道標/ch01_207 (2)/1.png
thumbnails/CH01_人生の道標/ChatGPT Image 2025年12月10日 21_01_09.png
thumbnails/projects.json
thumbnails/README.md
```

## 3. UI / API とファイルパスの対応

`ui/backend` 以下の FastAPI コードを読み、主要エンドポイントと対応する実際のファイルパスの対応表:

| Endpoint | 読み書きパス例 | 備考 |
|----------|----------------|------|
| `GET /api/channels/{channelId}/videos/{videoId}` | `script_pipeline/data/CH{channelId}/{videoId}/status.json` | 動画の進捗・メタ情報 |
| `GET /api/channels/{channelId}/videos/{videoId}/tts` | `script_pipeline/data/CH{channelId}/{videoId}/audio_prep/*.wav` | TTS 出力の音声ファイル |
| `GET /api/channels/{channelId}/videos/{videoId}/srt` | `script_pipeline/data/CH{channelId}/{videoId}/audio_prep/*.srt` | SRT 字幕ファイル |
| `GET /api/channels/{channelId}/videos/{videoId}/assembled` | `script_pipeline/data/CH{channelId}/{videoId}/content/assembled.md` | 台本の最終稿 |
| `GET /api/workspaces/thumbnails/{channelId}/{videoId}` | `thumbnails/CH{channelId}_...` | サムネイルファイル群 |
| `GET /api/planning` | `progress/channels/CH{channelId}.csv` | 進捗CSVファイル |
| `GET /api/ssot/persona/{channelId}` | `progress/personas/{channelId}_PERSONA.md` | チリペルソナ情報 |
| `GET /api/audio-tts-v2/...` | `audio_tts_v2/artifacts/final/CH{channelId}/{videoId}/` | 音声出力ファイル群 |

## 4. 注意点・既知の問題

* ディレクトリ構造が動画ごとに微妙に違う場合がある（例：`audio_prep` 内部のファイル構成）
* 一部の古い動画では `chunks` や `inference_metadata.txt` が存在しないケースがある
* 画像ファイル名に日本語や特殊文字を含むファイルが存在する（`"ChatGPT Image 2025年12月10日 21_01_09.png"`など）

## 5. 改善アイデア（任意）

* ディレクトリ構造を標準化し、すべての動画で同じファイル構造を持つように統一すると管理しやすくなる
* データパス解決のための共通ユーティリティ(`resolve_script_path`, `resolve_thumbnail_dir` など)を `ssot/paths/` に集約することで、参照間違いを減らせる
* 現行構造を維持しつつ、薄い抽象化レイヤーを導入してAPIとファイルパスの対応関係を明確化する
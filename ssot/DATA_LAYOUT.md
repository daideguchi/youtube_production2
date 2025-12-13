# DATA_LAYOUT — 現行データ格納構造の実態

## 1. 概要

- このドキュメントは、現在の youtube 制作パイプラインで
  「実際に参照される SoT/生成物/ログの格納場所」を記述する。
- 理想案ではなく、**2025-12-12 時点の事実**を優先する。
  （生成物の多くは gitignore 対象で、ディスク上にのみ存在する）

## 2. チャンネル別・動画別の生成データ

### 2.1 workspaces/scripts（台本SoT）

- ルート（正本）: `workspaces/scripts/CH{NN}/{VIDEO_NO}/`（互換: `script_pipeline/data/...`）
- 代表例:

#### CH07-025 の例

```text
workspaces/scripts/CH07/025
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
workspaces/scripts/CH05/029
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

### 2.2 workspaces/audio（音声成果物）

* ルート（正本）: `workspaces/audio/final/CH{NN}/{VIDEO_NO}/`（互換: `audio_tts_v2/artifacts/final/...`）

#### 例: 典型的な final 配下の構造

```text
workspaces/audio/final/CH02/033
├── a_text.txt
├── CH02-033.wav
├── CH02-033.srt
└── log.json
```

※ `*.wav` / `*.srt` は gitignore 対象で、通常はディスク上にのみ存在する。

### 2.3 commentary_02_srt2images_timeline

* ルート: `commentary_02_srt2images_timeline/...`
* 役割: SRT → 画像タイムライン生成
* 生成結果（run_dir）: `workspaces/video/runs/<run_id>/`（互換: `commentary_02_srt2images_timeline/output/<run_id>/`）
  - `image_cues.json`, `images/*.png`, `capcut_draft`（CapCutプロジェクトへのsymlink）, `capcut_draft_info.json`, `auto_run_info.json` など
  - `runs/` は `workspaces/.gitignore` で gitignore 対象
* 入力キャッシュ: `workspaces/video/input/`（互換: `commentary_02_srt2images_timeline/input/`、gitignore 対象）

### 2.4 workspaces/planning/channels（企画CSV）

* ルート: `workspaces/planning/channels/`（互換: `progress/channels/`）

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

補足:
- サムネの追跡SoTは `thumbnails/projects.json`（採用/バリアント/画像パス等）。
- UI/Backend は `/thumbnails/assets/...` を配信する設計で、物理パスは `thumbnails/assets/...` に寄せる想定（未整備/移行中の可能性あり）。
- `thumbnails/CHxx_<チャンネル名>/...` は旧来の資産配置として残っているため、移行/アーカイブ方針を `ssot/PLAN_REPO_DIRECTORY_REFACTOR.md` と `ssot/PLAN_OPS_ARTIFACT_LIFECYCLE.md` で確定させる。

## 3. UI / API とファイルパスの対応

`ui/backend` 以下の FastAPI コードを読み、主要エンドポイントと対応する実際のファイルパスの対応表:

| Endpoint | 主な読み書きパス | 備考 |
|----------|------------------|------|
| `GET /api/planning` | `workspaces/planning/channels/CHxx.csv`（互換: `progress/channels/...`） | 企画/進捗CSV（Planning SoT） |
| `GET /api/ssot/persona/{channel}` / `PUT /api/ssot/persona/{channel}` | `workspaces/planning/personas/CHxx_PERSONA.md`（互換: `progress/personas/...`） | Persona SoT |
| `GET /api/channels/{channel}/videos/{video}` | `workspaces/scripts/CHxx/NNN/status.json` / `content/assembled.md`（互換: `script_pipeline/data/...`） | 台本SoT |
| `PUT /api/channels/{channel}/videos/{video}/assembled` | `workspaces/scripts/CHxx/NNN/content/assembled.md`（互換: `script_pipeline/data/...`） | 人間編集の正本 |
| `GET /api/channels/{channel}/videos/{video}/audio` | `workspaces/audio/final/CHxx/NNN/CHxx-NNN.wav`（互換: `audio_tts_v2/artifacts/final/...`） | 下流参照の音声SoT |
| `GET /api/channels/{channel}/videos/{video}/srt` / `PUT /api/auto-draft/srt` | `workspaces/audio/final/CHxx/NNN/CHxx-NNN.srt`（互換: `audio_tts_v2/artifacts/final/...`） | 字幕SoT（UI編集可） |
| `POST /api/auto-draft/create` | `workspaces/video/runs/<run_id>/...`（互換: `commentary_02_srt2images_timeline/output/...`） | SRT→画像→CapCutドラフト生成 |
| `GET /api/workspaces/thumbnails` | `thumbnails/projects.json` | サムネSoT |
| `GET /thumbnails/assets/{...}` | `thumbnails/assets/...` | 静的配信（移行中の可能性あり） |

## 4. 注意点・既知の問題

* ディレクトリ構造が動画ごとに微妙に違う場合がある（例：`audio_prep` 内部のファイル構成）
* 一部の古い動画では `chunks` や `inference_metadata.txt` が存在しないケースがある
* 画像ファイル名に日本語や特殊文字を含むファイルが存在する（`"ChatGPT Image 2025年12月10日 21_01_09.png"`など）

## 5. 改善アイデア（任意）

* ディレクトリ構造を標準化し、すべての動画で同じファイル構造を持つように統一すると管理しやすくなる
* データパス解決のための共通ユーティリティ（repo/workspaces SoT）は `factory_common/paths.py` に集約し、直書きパスを段階的に廃止する（`ssot/PLAN_STAGE1_PATH_SSOT_MIGRATION.md`）。
* 現行構造を維持しつつ、薄い抽象化レイヤーを導入してAPIとファイルパスの対応関係を明確化する

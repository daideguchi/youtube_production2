# CLI Design Policy - factory-commentary

## 1. CLI名称と使用ルール（factory-ch02 禁止）

このリポジトリの共通CLI名は `factory-commentary` とします。

### 使い方：
```bash
factory-commentary <channel_id> <srt_path> {new|draft|check}
```

例：
- `factory-commentary CH02 commentary_02_哲学系/CH02-015.srt check`
- `factory-commentary CH02 commentary_02_哲学系/CH02-015.srt new`
- `factory-commentary CH02 commentary_02_哲学系/CH02-015.srt draft`

### 禁止事項：
- `factory-ch02` というCLIエイリアスは存在していても、**Codexは絶対に使ってはいけません。**
- 新たに `factory-ch01` や `factory-ch08` 等のCLIエントリポイントを追加することも禁止です。
- チャンネル固有の違いは、必ず `channel_id` と presets/config（`channel_presets.json` 等）で吸収してください。

## 2. run_dir / resume の運用ルール（1本道）

### 2.1 run_dir の命名
- 1回の run は 1つのディレクトリに対応します。
- ディレクトリ名は `<VIDEO_ID>_<TIMESTAMP>` 形式とします。
  - 例：`CH02-015_20251210_191904`
- その中に以下のファイルが入ります：
  - `CH02-015.srt`
  - `channel_preset.json`
  - `image_cues.json`
  - `belt_config.json`（帯生成成功時）
  - `persona.txt`
  - `guides/`, `images/`, `logs/`

### 2.2 正しい run_dir の選び方
- 任意の動画ID（例: CH02-015）について、「現在の有効run」は次の条件で決めます：
  - `output/` 以下のディレクトリのうち、
    - ディレクトリ名が `CH02-015_...` で始まり、
    - `image_cues.json` が存在するもの
    をタイムスタンプ順でソートし、**一番新しいもの**を選ぶ。
- CLI (`factory-commentary`) は、internalに `find_latest_run_dir(video_id)` を持ち、
  `draft` 時には **必ずそれを使って auto_capcut_run に渡す**こと。

### 2.3 intent ごとの動き

#### `check`:
- `run_pipeline` を engine=none, cue-mode=grouped 等で実行し、新しい run_dir を作る。
- 同じ run_dir 内で `belt_generation` を行い、`belt_config.json` を作成。
- CapCut draft は作成しない。
- ログに `[VIDEO_ID][run_pipeline][OK] run_dir=...` および `[VIDEO_ID][belt_generation][OK/FALLBACK] ...` を出す。

#### `new`:
- 上記 `check` 相当の処理 + 必要なら画像生成も行う。
- その run_dir を使って `auto_capcut_run` を呼び出し、CapCut draft を生成。
- `auto_capcut_run` 呼び出しには `--draft-root <run_dir>` のような形で run_dir を明示的に渡すこと。
- `auto_capcut_run` の `--resume` は使用禁止。

#### `draft`:
- 新しい run は作らない。
- `find_latest_run_dir(video_id)` で run_dir を決める。
- 見つからなければ ERROR を出して終了（新しい run_dir は作らない）。
- 見つかった場合、その run_dir を `auto_capcut_run` に渡して CapCut draft のみを再生成。

### 2.4 `--resume` の扱い
- auto_capcut_run.py の `--resume` オプションは、挙動が不透明でランダムな新規 run_dir を作る原因となるため、
  Codexは **使用してはいけません。**
- 再生成が必要な場合は、常に CLI (`factory-commentary`) の `draft` intent を使い、
  上記ルールにより選ばれた run_dir を明示的に渡してください。
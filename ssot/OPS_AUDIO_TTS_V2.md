# OPS_AUDIO_TTS_V2 — 音声/TTS（strict）運用手順（正本/中間/掃除）

この文書は「音声とSRTを作る/直す/後片付けする」を運用手順として固定する。  
処理フロー/I/Oの正本は `ssot/OPS_CONFIRMED_PIPELINE_FLOW.md`。

---

## 0. SoT（正本）

- 入力（正）: `workspaces/scripts/{CH}/{NNN}/` 配下の **最終確定入力**（互換: `script_pipeline/data/...`。UI/Backend の解決順）
  1) `audio_prep/script_audio_human.txt`
  2) `content/script_audio_human.txt`
  3) `content/assembled_human.md`
  4) `audio_prep/script_sanitized.txt`
  5) `content/script_audio.txt`
  6) `content/assembled.md`
  - 原則: **人間が介入した最新版（*_human）を最優先**にする。
- 出力（下流参照の正）: `workspaces/audio/final/{CH}/{NNN}/`（互換: `audio_tts_v2/artifacts/final/...`）
  - `{CH}-{NNN}.wav`
  - `{CH}-{NNN}.srt`
  - `log.json`

---

## 1. 入口（Entry points）

### 1.1 推奨（UI / Backend 経由）
- `POST /api/audio-tts-v2/run-from-script`（input_path の指定不要。上記「最終確定入力」を backend 側で解決）
  - UI: Episode Studio / 音声ワークスペースの「TTS実行」
  - 返却: `/api/channels/{CH}/videos/{NNN}/audio|srt|log` の URL を返す（ファイルパスではない）

### 1.2 推奨（script_pipeline 経由）
- `python -m script_pipeline.cli audio --channel CH06 --video 033`
  - 途中再開（chunksを再利用）: `... --resume`

### 1.2 直叩き（audio_tts_v2）
- `python audio_tts_v2/scripts/run_tts.py --channel CH06 --video 033 --input workspaces/scripts/CH06/033/content/assembled.md`（互換: `script_pipeline/data/...`）

---

## 2. 使い方（よくある運用）

### 2.1 読みだけ先に確認（prepass）
- `python audio_tts_v2/scripts/run_tts.py ... --prepass`
  - 目的: wavを作らず `log.json` を作って読み候補を監査する
  - 監査手順は `ssot/OPS_TTS_MANUAL_READING_AUDIT.md`

### 2.2 一部だけ作り直す（indices）
- `... --indices 3,10`（0-based）
  - 目的: 誤読セグメントだけ再生成して結合する

---

## 3. 中間生成物（audio_prep）の位置づけ

- `workspaces/scripts/{CH}/{NNN}/audio_prep/` は **strict run_tts の作業領域（L2/L3）**（互換: `script_pipeline/data/...`）
  - 容量最大: `audio_prep/chunks/*.wav`
  - finalが揃ったら原則削除して良い（保持/削除の正本は `ssot/PLAN_OPS_ARTIFACT_LIFECYCLE.md`）
  - UI/Backend 経由の TTS 成功時は **自動で chunks を削除**（下記参照）

---

## 4. 後片付け（容量対策・安全ガード付き）

### 4.0 自動cleanup（UI/Backend 経由の TTS 成功時）
backend (`apps/ui-backend/backend/main.py:_run_audio_tts_v2`) は成功時にベストエフォートで以下を実行する。

- `workspaces/scripts/{CH}/{NNN}/audio_prep/chunks/` を削除（互換: `script_pipeline/data/...`）
- `workspaces/scripts/{CH}/{NNN}/audio_prep/{CH}-{NNN}.wav|.srt`（重複バイナリ）を削除
- `workspaces/audio/final/{CH}/{NNN}/chunks/` を削除（巨大。再生成可能。互換: `audio_tts_v2/artifacts/final/.../chunks/`）
  - 無効化: `YTM_TTS_KEEP_CHUNKS=1`

### 4.1 finalへ不足を同期（削除前の安全策）
- `python3 scripts/sync_audio_prep_to_final.py --run --keep-recent-minutes 360`
  - finalに wav/srt/log/a_text が無い場合のみコピー（上書きしない）

### 4.2 chunks削除（最大容量）
- `python3 scripts/cleanup_audio_prep.py --run --keep-recent-minutes 360`
  - 条件: final_wav または audio_prep直下wav が存在するもののみ

### 4.3 audio_prep の重複wav/srt削除（finalが正になった後）
- `python3 scripts/purge_audio_prep_binaries.py --run --keep-recent-minutes 360`

### 4.4 final の chunks 削除（容量最大）
- `python3 scripts/purge_audio_final_chunks.py --run --keep-recent-minutes 360`

---

## 5. 例外（要注意）

### 5.1 chunksだけ残ってfinalが無い
例: `workspaces/scripts/CH02/018/audio_prep/chunks/` のような状態（互換: `script_pipeline/data/...`）。  
これは「生成途中で止まった/merge前に中断」等の可能性があるため、**即削除しない**。

対処:
- まず `status.json` を確認し、意図して未完了か判定する
- 必要なら `--resume` で再開して final を作ってから cleanup する

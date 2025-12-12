# OPS_AUDIO_TTS_V2 — 音声/TTS（strict）運用手順（正本/中間/掃除）

この文書は「音声とSRTを作る/直す/後片付けする」を運用手順として固定する。  
処理フロー/I/Oの正本は `ssot/OPS_CONFIRMED_PIPELINE_FLOW.md`。

---

## 0. SoT（正本）

- 入力（正）: `script_pipeline/data/{CH}/{NNN}/content/assembled.md`
- 出力（下流参照の正）: `audio_tts_v2/artifacts/final/{CH}/{NNN}/`
  - `{CH}-{NNN}.wav`
  - `{CH}-{NNN}.srt`
  - `log.json`

---

## 1. 入口（Entry points）

### 1.1 推奨（script_pipeline 経由）
- `python -m script_pipeline.cli audio --channel CH06 --video 033`
- 途中再開（chunksを再利用）: `... --resume`

### 1.2 直叩き（audio_tts_v2）
- `python audio_tts_v2/scripts/run_tts.py --channel CH06 --video 033 --input script_pipeline/data/CH06/033/content/assembled.md`

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

- `script_pipeline/data/{CH}/{NNN}/audio_prep/` は **strict run_tts の作業領域（L2/L3）**
  - 容量最大: `audio_prep/chunks/*.wav`
  - finalが揃ったら原則削除して良い（保持/削除の正本は `ssot/PLAN_OPS_ARTIFACT_LIFECYCLE.md`）

---

## 4. 後片付け（容量対策・安全ガード付き）

### 4.1 finalへ不足を同期（削除前の安全策）
- `python3 scripts/sync_audio_prep_to_final.py --run --keep-recent-minutes 360`
  - finalに wav/srt/log/a_text が無い場合のみコピー（上書きしない）

### 4.2 chunks削除（最大容量）
- `python3 scripts/cleanup_audio_prep.py --run --keep-recent-minutes 360`
  - 条件: final_wav または audio_prep直下wav が存在するもののみ

### 4.3 audio_prep の重複wav/srt削除（finalが正になった後）
- `python3 scripts/purge_audio_prep_binaries.py --run --keep-recent-minutes 360`

---

## 5. 例外（要注意）

### 5.1 chunksだけ残ってfinalが無い
例: `script_pipeline/data/CH02/018/audio_prep/chunks/` のような状態。  
これは「生成途中で止まった/merge前に中断」等の可能性があるため、**即削除しない**。

対処:
- まず `status.json` を確認し、意図して未完了か判定する
- 必要なら `--resume` で再開して final を作ってから cleanup する


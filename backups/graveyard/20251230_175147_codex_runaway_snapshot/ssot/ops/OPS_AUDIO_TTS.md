# OPS_AUDIO_TTS — 音声/TTS（strict）運用手順（正本/中間/掃除）

この文書は「音声とSRTを作る/直す/後片付けする」を運用手順として固定する。  
処理フロー/I/Oの正本は `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`。

---

## 0. SoT（正本）

- 入力（正 / AテキストSoT）:
  - 優先: `workspaces/scripts/{CH}/{NNN}/content/assembled_human.md`
  - 代替: `workspaces/scripts/{CH}/{NNN}/content/assembled.md`
  - ルール:
    - 標準の音声生成（`run_tts` / `/api/audio-tts/run-from-script`）は **AテキストSoTのみ**を入力にする（暗黙フォールバック禁止）。
    - `assembled_human.md` が存在する場合はそれが正本（`assembled.md` はミラー/互換入力）。
      - `assembled_human.md` が新しい → `run_tts` が `assembled.md` に自動同期（安全）
      - `assembled.md` が新しい（または同時刻）かつ内容差分 → **停止（CONFLICT）**。明示解決してから進む（事故防止）。
        - 解決（例）:
          - `python3 scripts/episode_ssot.py confirm-a --channel CH02 --video 014 --prefer human`
          - `python3 scripts/episode_ssot.py confirm-a --channel CH02 --video 014 --prefer assembled`
- Bテキスト（TTS入力 / 派生・必ず materialize）:
  - `workspaces/scripts/{CH}/{NNN}/audio_prep/script_sanitized.txt`
  - `run_tts` が毎回 A から生成して書き出す（サニタイズ失敗でも raw を書いて **必ず生成**）。
  - 人手でBを編集して再生成する場合は **明示入力**（UIの「音声用テキスト保存→再生成」）として扱い、無ければ失敗（Aへ戻さない）。
  - 安全ガード: `run_tts` は **BがAより古く、かつ sanitize(A) と不一致** の場合は停止する（Bの取り残し事故防止）。
- 出力（下流参照の正）: `workspaces/audio/final/{CH}/{NNN}/`
  - `{CH}-{NNN}.wav`（strict。旧運用では `.flac` 等もある）
  - `{CH}-{NNN}.srt`
  - `log.json`
  - `a_text.txt`（**実際に合成したTTS入力（=Bテキスト）のスナップショット**）
  - `audio_manifest.json`（契約）

---

## 1. 入口（Entry points）

### 1.1 推奨（UI / Backend 経由）
- `POST /api/audio-tts/run-from-script`（input_path の指定不要。上記「AテキストSoT」を backend 側で解決）
  - UI: Episode Studio / 音声ワークスペースの「TTS実行」
  - 返却: `/api/channels/{CH}/videos/{NNN}/audio|srt|log` の URL を返す（ファイルパスではない）

### 1.2 推奨（script_pipeline 経由）
- `python -m script_pipeline.cli audio --channel CH06 --video 033`
  - 途中再開（chunksを再利用）: `... --resume`

### 1.3 直叩き（audio_tts）
- `PYTHONPATH=".:packages" python3 -m audio_tts.scripts.run_tts --channel CH06 --video 033 --input workspaces/scripts/CH06/033/content/assembled.md`

### 1.4 整合ガード（Planning ↔ Script）
- `run_tts` は `workspaces/scripts/{CH}/{NNN}/status.json: metadata.alignment`（schema=`ytm.alignment.v1`）を検証し、**無い/不一致なら停止**する（誤台本で音声を作らないため）。
- 修復:
  - `python scripts/enforce_alignment.py --channels CHxx --apply`（整合スタンプを再作成）
  - もしくは `python -m script_pipeline.cli reconcile --channel CHxx --video NNN`（台本/進捗の再整合→スタンプ更新）

---

## 2. 使い方（よくある運用）

### 2.1 読みだけ先に確認（prepass）
- `PYTHONPATH=".:packages" python3 -m audio_tts.scripts.run_tts ... --prepass`
  - 目的: wavを作らず `log.json` を作って読み候補を監査する
  - 監査手順は `ssot/ops/OPS_TTS_MANUAL_READING_AUDIT.md`

### 2.2 一部だけ作り直す（indices）
- `... --indices 3,10`（0-based）
  - 目的: 誤読セグメントだけ再生成して結合する

---

## 3. 中間生成物（audio_prep）の位置づけ

- `workspaces/scripts/{CH}/{NNN}/audio_prep/` は **strict run_tts の作業領域（L2/L3）**
  - 容量最大: `audio_prep/chunks/*.wav`
  - finalが揃ったら原則削除して良い（保持/削除の正本は `ssot/plans/PLAN_OPS_ARTIFACT_LIFECYCLE.md`）
  - UI/Backend 経由の TTS 成功時は **自動で chunks を削除**（下記参照）

---

## 4. 後片付け（容量対策・安全ガード付き）

### 4.0 自動cleanup（UI/Backend 経由の TTS 成功時）
backend (`apps/ui-backend/backend/main.py:_run_audio_tts`) は成功時にベストエフォートで以下を実行する。

- `workspaces/scripts/{CH}/{NNN}/audio_prep/chunks/` を削除
- `workspaces/scripts/{CH}/{NNN}/audio_prep/{CH}-{NNN}.wav|.srt`（重複バイナリ）を削除
- `workspaces/audio/final/{CH}/{NNN}/chunks/` を削除（巨大。再生成可能。）
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
例: `workspaces/scripts/CH02/018/audio_prep/chunks/` のような状態。  
これは「生成途中で止まった/merge前に中断」等の可能性があるため、**即削除しない**。

対処:
- まず `status.json` を確認し、意図して未完了か判定する
- 必要なら `--resume` で再開して final を作ってから cleanup する

---

## 6. ポーズ（strict の解釈）

- 通常のつなぎ（文末の最小ポーズ）: **0.1秒**
- `---`（1行単独）: **0.5秒**
- 空行/改行: ポーズ指示として扱わない（文章の整形用途）

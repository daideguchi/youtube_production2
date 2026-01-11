# OPS_AUDIO_TTS — 音声/TTS（strict）運用手順（正本/中間/掃除）

この文書は「音声とSRTを作る/直す/後片付けする」を運用手順として固定する。  
処理フロー/I/Oの正本は `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`。

---

## 0. SoT（正本）

- 入力（正 / AテキストSoT）:
  - 優先: `workspaces/scripts/{CH}/{NNN}/content/assembled_human.md`
  - 代替: `workspaces/scripts/{CH}/{NNN}/content/assembled.md`
  - ルール:
    - 標準の音声生成（`run_tts` / `/api/audio-tts/run-from-script`）は **AテキストSoT** を入力にする（暗黙フォールバック禁止）。
    - `assembled_human.md` が存在する場合はそれが正本、`assembled.md` は互換用の mirror。
    - split-brain（`assembled_human.md` と `assembled.md` が差分）:
      - human が新しい: `assembled.md` を human に同期（.bak付き）
      - assembled が新しい（または同時刻）: **STOP**（明示解決が必要）
- Bテキスト（TTS入力 / 派生・必ず materialize）:
  - `workspaces/scripts/{CH}/{NNN}/audio_prep/script_sanitized.txt`
  - `run_tts` は毎回 `audio_prep/script_sanitized.txt` を materialize して書き出す（サニタイズ失敗でも raw を書いて **必ず生成**）。
  - Bを入力にして再生成する場合（UIの「音声用テキスト保存→再生成」など）は **明示入力**として扱う（無ければ失敗。Aへ戻さない）。
  - さらに safety: Bが `sanitize(A)` と一致せず、かつ BがAより古い場合は **STOP（STALE）**（誤台本で合成しない）。
- 出力（下流参照の正）: `workspaces/audio/final/{CH}/{NNN}/`
  - `{CH}-{NNN}.wav`（strict。旧運用では `.flac` 等もある）
  - `{CH}-{NNN}.srt`
  - `log.json`
  - `a_text.txt`（**実際に合成したTTS入力（=Bテキスト）のスナップショット**）
  - `audio_manifest.json`（契約）
- Voicepeak user dict（GUIの辞書を repo と揃える用途）:
  - SoT: `packages/audio_tts/data/voicepeak/dic.json`
  - 自動: `run_tts` は engine=voicepeak のとき、実行開始時に上記 SoT を best-effort でローカル設定へ **追記同期（add-only）** する（人間がローカルで追加した辞書は消さない）。
  - Sync: `python3 -m audio_tts.scripts.sync_voicepeak_user_dict [--dry-run]`
  - Destination: `~/Library/Application Support/Dreamtonics/Voicepeak/settings/dic.json`
  - strict 読み置換: ローカル `dic.json` に加えて `~/Library/Application Support/Dreamtonics/Voicepeak/settings/user.csv` も best-effort で取り込み（安全な語のみ）
- VOICEVOX user dict（公式ユーザー辞書 / ローカル確認用）:
  - SoT（repo / strict側の読み置換）:
    - グローバル: `packages/audio_tts/configs/learning_dict.json`（全CH共通。ユニーク誤読のみ）
    - チャンネル: `packages/audio_tts/data/reading_dict/CHxx.yaml`（そのCHで読みが一意な語のみ）
    - 動画ローカル（その回だけ）:
      - **原則**: Bテキスト（`audio_prep/script_sanitized.txt`）をカナ表記にして個別対応
      - 文脈で読みを割る必要がある場合: `audio_prep/local_token_overrides.json`（位置指定）
      - `audio_prep/local_reading_dict.json`（surface→readingの一括置換）は **原則使わない**（台本内で一意に固定できる語だけ）
  - Sync（repo → engine）: `PYTHONPATH=".:packages" python3 -m audio_tts.scripts.sync_voicevox_user_dict --channel CHxx`
    - 注: 安全語のみ反映・衝突（チャンネル間で読みが違う語）は skip（固定ルール: `ssot/DECISIONS.md` の D-014）
- Voicepeak CLI 安定化（クラッシュ抑制）:
  - 既定: VOICEPEAK の同時起動で落ちやすいため、CLI 呼び出しを **プロセス間ロックで直列化** する（multi-agent安全）。
  - 調整（必要時のみ）: `VOICEPEAK_CLI_TIMEOUT_SEC`, `VOICEPEAK_CLI_RETRY_COUNT`, `VOICEPEAK_CLI_RETRY_SLEEP_SEC`, `VOICEPEAK_CLI_COOLDOWN_SEC`
  - 例外: `VOICEPEAK_CLI_GLOBAL_LOCK=0` で直列化を無効化（非推奨）
- 読点（、）の間引き（Voicepeakテンポ改善）:
  - `packages/script_pipeline/audio/channels/<CH>/voice_config.json` の voicepeak `engine_options` に `comma_policy: "particles"` を設定すると、`は/が/に/で/も/へ/を` の直後の `、` を strict 側で間引く（字幕テキストは維持、読み入力のみ変更）。

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
- `PYTHONPATH=".:packages" python3 -m audio_tts.scripts.run_tts --channel CH06 --video 033 --input workspaces/scripts/CH06/033/content/assembled_human.md`（無ければ `assembled.md`）

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

# OPS_AUDIO_TTS — 音声/TTS（strict）運用手順（正本/中間/掃除）

この文書は「音声とSRTを作る/直す/後片付けする」を運用手順として固定する。  
処理フロー/I/Oの正本は `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`。

---

## TL;DR（結論 / 最小構造）

- **Aテキスト（台本本文）**は表示/内容の正本。TTS都合で書き換えない（誤字/崩れのみ例外）。
- **Bテキスト（TTS入力）**は A から生成する派生物。読み最適化（アノテーション/辞書/override）は **B側に集約**する。
- 辞書の正本は **repo**。VOICEVOX/VOICEPEAK の公式ユーザー辞書は **syncで配布先**にする（手でいじり続けない）。
- 辞書の箱はこの4つだけに収束させる（上が安全/下が例外、下ほど強い）:
  1) `packages/audio_tts/data/global_knowledge_base.json`（全CH共通・確定語のみ）
  2) `packages/audio_tts/data/reading_dict/CHxx.yaml`（そのCHでのみ確定語）
  3) `audio_prep/local_reading_dict.json`（その回だけ・**フレーズ**のみ。単語単体は禁止）
  4) `audio_prep/local_token_overrides.json`（文脈依存/曖昧語の最終手段。位置指定）
- `packages/audio_tts/configs/learning_dict.json` は **自動学習/補助**（strict B生成には使う）が、公式辞書へは自動同期しない。

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
- 自動: `run_tts` は engine=voicepeak のとき、実行開始時に上記 SoT をローカル設定へ **追記同期（add-only）** する（追記同期は試行。失敗しても run_tts は継続／人間がローカルで追加した辞書は消さない）。
  - Sync: `python3 -m audio_tts.scripts.sync_voicepeak_user_dict [--dry-run]`
  - Destination: `~/Library/Application Support/Dreamtonics/Voicepeak/settings/dic.json`
- strict 読み置換: ローカル `dic.json` に加えて `~/Library/Application Support/Dreamtonics/Voicepeak/settings/user.csv` も取り込みを試行（安全な語のみ）
- VOICEVOX user dict（公式ユーザー辞書 / ローカル確認用）:
  - SoT（repo / strict側の読み置換）:
    - グローバル（確定/手でレビューして昇格させる領域）: `packages/audio_tts/data/global_knowledge_base.json`
    - グローバル（自動学習/補助。公式辞書へは自動同期しない）: `packages/audio_tts/configs/learning_dict.json`
    - チャンネル: `packages/audio_tts/data/reading_dict/CHxx.yaml`（そのCHで読みが一意な語のみ）
    - 動画ローカル（その回だけ）:
      - 標準: Bテキスト（`audio_prep/script_sanitized.txt`）をカナ表記にして個別対応
      - 文脈で読みを割る必要がある場合: `audio_prep/local_token_overrides.json`（位置指定）
      - `audio_prep/local_reading_dict.json`（surface→readingの一括置換）は **例外的に使用可**（その回で一意/安全な“フレーズ”のみ。単語単体・曖昧語は禁止。再発したら昇格）
  - Sync（repo → engine / 入口固定）: `PYTHONPATH=".:packages" python3 -m audio_tts.scripts.sync_voicevox_user_dict --global-only --overwrite`
  - Sync（必要時）: `PYTHONPATH=".:packages" python3 -m audio_tts.scripts.sync_voicevox_user_dict --channel CHxx --overwrite`
    - 注: 安全語のみ反映・衝突（チャンネル間で読みが違う語）は skip（固定ルール: `ssot/DECISIONS.md` の D-014）
- Voicepeak CLI 安定化（クラッシュ抑制）:
  - 既定: VOICEPEAK の同時起動で落ちやすいため、CLI 呼び出しを **プロセス間ロックで直列化** する（multi-agent安全）。
  - 調整（必要時のみ）: `VOICEPEAK_CLI_TIMEOUT_SEC`, `VOICEPEAK_CLI_RETRY_COUNT`, `VOICEPEAK_CLI_RETRY_SLEEP_SEC`, `VOICEPEAK_CLI_COOLDOWN_SEC`
  - 例外: `VOICEPEAK_CLI_GLOBAL_LOCK=0` で直列化を無効化（注意: 同時起動でクラッシュしやすい）
- 読点（、）の間引き（Voicepeakテンポ改善）:
  - `packages/script_pipeline/audio/channels/<CH>/voice_config.json` の voicepeak `engine_options` に `comma_policy: "particles"` を設定すると、`は/が/に/で/も/へ/を` の直後の `、` を strict 側で間引く（字幕テキストは維持、読み入力のみ変更）。

---

## 1. 入口（Entry points）

### 1.1 入口固定（UI / Backend 経由）
- `POST /api/audio-tts/run-from-script`（input_path の指定不要。上記「AテキストSoT」を backend 側で解決）
  - UI: Episode Studio / 音声ワークスペースの「TTS実行」
  - 返却: `/api/channels/{CH}/videos/{NNN}/audio|srt|log` の URL を返す（ファイルパスではない）

### 1.2 入口固定（script_pipeline 経由）
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
  - 監査/修正の正本は `ssot/ops/OPS_TTS_ANNOTATION_FLOW.md`（engine別の合格条件まで固定）

### 2.2 一部だけ作り直す（indices）
- `... --indices 3,10`（0-based）
  - 目的: 誤読セグメントだけ再生成して結合する

---

## 3. 中間生成物（audio_prep）の位置づけ

- `workspaces/scripts/{CH}/{NNN}/audio_prep/` は **strict run_tts の作業領域（L2/L3）**
  - 容量最大: `audio_prep/chunks/*.wav`
  - finalが揃ったら削除してよい（保持/削除の正本は `ssot/plans/PLAN_OPS_ARTIFACT_LIFECYCLE.md`）
  - UI/Backend 経由の TTS 成功時は **自動で chunks を削除**（下記参照）

---

## 4. 後片付け（容量対策・安全ガード付き）

### 4.0 自動cleanup（UI/Backend 経由の TTS 成功時）
backend (`apps/ui-backend/backend/main.py:_run_audio_tts`) は成功時に以下を試行する（失敗しても停止しない）。

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
- final が無い場合は `--resume` で再開して final を作ってから cleanup する

---

## 6. ポーズ（strict の解釈）

- 通常のつなぎ（文末の最小ポーズ）: **0.1秒**
- `---`（1行単独）: **0.5秒**
- 空行/改行: ポーズ指示として扱わない（文章の整形用途）

---

## 7. エンジン別：アノテーションの流れ / Bテキストの理想系（確定）

ここで言う「アノテーション」は **Aテキストを書き換えることではない**。  
`run_tts` が A（SoT）から **B（TTS入力）を決定的に materialize** する工程（辞書/override/正規化）を指す。

詳細な確定フロー/辞書運用ルールは `ssot/ops/OPS_TTS_ANNOTATION_FLOW.md` を正本とする。

重要（迷子防止 / 固定）:
- **エンジンは自動決定**（`packages/script_pipeline/audio/channels/CHxx/voice_config.json` → `script_override` → default）。通常運用で `ENGINE_DEFAULT_OVERRIDE` や `--engine-override` を使わない。
- **VOICEVOX**: `--prepass` で **mismatch=0** を機械的に証明できる（=誤読ゼロを決定論で固定できる）。
- **VOICEPEAK**: `audio_query.kana` が無いので mismatch=0 は証明できない。代わりに **B側を安全形に寄せる + サンプル再生で確認**（手順は 7.3）。

### 7.0 運用フロー（標準 / 全話を効率よく回す）

1) **prepassで“読めるか”だけ先に潰す**（wavを作らない）  
   - 単発: `./ops audio -- --channel CHxx --video NNN --prepass`
   - `python3 scripts/batch_regenerate_tts.py --channel CHxx --prepass --skip-tts-reading --min-video 1 --max-video 30 [--allow-unvalidated]`  
   - `--skip-tts-reading` は auditor（読みLLM）を無効化するフラグ（mismatchは検出してfail-fastする）。
2) mismatch が出たら、修正は **B側**で次の順に最小で行う  
   - 全CH共通で一意（例: `口業→クゴウ`）→ `global_knowledge_base.json`  
   - CH内だけ一意 → `reading_dict/CHxx.yaml`  
   - その回だけ一意（**フレーズ**）→ `audio_prep/local_reading_dict.json`  
   - 文脈依存/曖昧語 → `audio_prep/local_token_overrides.json`（位置指定）
3) もう一度 prepass → mismatch=0 を確認 → その後に合成（wav/srt）へ進む  
4) 公式辞書へ反映したい確定語は、**グローバル確定語だけ**同期  
   - `PYTHONPATH=".:packages" python3 -m audio_tts.scripts.sync_voicevox_user_dict --global-only --overwrite`

### 7.1 共通（VOICEVOX/VOICEPEAK）

**Bテキストの理想（共通）**
- Aの意味/表現を変えない（字幕/内容SoTはA）。Bは **読みやすさ・誤読ゼロ** のための派生物。
- **メタ除去済み**（URL/脚注/出典/注釈ラベル等は入れない）。
- **読み併記は入れない**（例: `刈羽郡、かりわぐん` / `大河内正敏（おおこうちまさとし）` はB生成で重複側を落とす）。
- **数字/英字はB側で決定的にカナ化**（Aはそのままでも良い）。
- 同一入力（A + 辞書/override + 設定）なら **Bが常に同じ**（再現性100%）。
- 正本スナップショット:
  - `workspaces/audio/final/{CH}/{NNN}/a_text.txt`（実際に合成したB）
  - `workspaces/scripts/{CH}/{NNN}/audio_prep/script_sanitized.txt`（Bの作業版）

**辞書のSoT（共通）**
- グローバル（全CH共通の確定語 / 公式辞書へ同期OK）: `packages/audio_tts/data/global_knowledge_base.json`
- グローバル（自動学習/補助。公式辞書へは自動同期しない）: `packages/audio_tts/configs/learning_dict.json`
- チャンネル（そのCHでのみ確定語）: `packages/audio_tts/data/reading_dict/CHxx.yaml`
- Voicepeak辞書SoT（GUI/CLIの辞書）: `packages/audio_tts/data/voicepeak/dic.json`
  - 注: strict のB生成で取り込むのは **VOICEPEAK時のみ**（安全語のみ）。VOICEVOXには混ぜない。ローカルVoicepeak辞書は add-only sync で維持。

**辞書の読み込み順（arbiter / B生成）**
`audio_tts.tts.arbiter.resolve_readings_strict` が、B生成用の辞書を以下の順でマージする（後勝ち）:
1) `global_knowledge_base.json`（KB） + `learning_dict.json`
2) チャンネル辞書 `reading_dict/CHxx.yaml`（安全語のみ）
3) 動画ローカル `audio_prep/local_reading_dict.json`（安全語のみ）
4) 位置指定 `audio_prep/local_token_overrides.json`（最優先・token indexで適用）

※ **VOICEPEAK のみ** 追加で以下を取り込む（VOICEVOXには混ぜない / 機械ローカル状態の混入防止）:
- Voicepeak辞書（repo `dic.json` → local `dic.json` → local `user.csv` の順で上書き; 安全語のみ）

**辞書/override の安全フィルタ（固定ルール）**
- surface（キー）:
  - 1文字は禁止（誤爆しやすい）
  - 文脈で読みが揺れる語は禁止（例: `十分`, `行ったり`）
  - 実装: `audio_tts.tts.reading_dict.is_banned_surface`
- reading（値）:
  - **カナのみ**（漢字が混ざるreadingは無効）
  - 実装: `audio_tts.tts.reading_dict.is_safe_reading`

**B生成（共通）**
1) AテキストSoTを解決（`assembled_human.md -> assembled.md`）  
2) strict segmentation（`audio_tts.tts.strict_segmenter`）  
3) MeCab tokenize（`audio_tts.tts.mecab_tokenizer`）  
4) 読み解決（辞書/override）→ Bを確定（`audio_tts.tts.arbiter.resolve_readings_strict`）
   - 優先順位（高→低）:
     - `audio_prep/local_token_overrides.json`（位置指定。曖昧語の最終手段）
     - `audio_prep/local_reading_dict.json`（surface→reading。安全語のみ）
     - `packages/audio_tts/data/reading_dict/CHxx.yaml`（一意の読みのみ）
     - グローバル（全CH共通）:
       - `packages/audio_tts/data/global_knowledge_base.json`（確定語/レビュー済。公式辞書にも同期OK）
       - `packages/audio_tts/configs/learning_dict.json`（自動学習/補助。公式辞書へは自動同期しない）
   - 固定アノテーション（Bのみ）:
     - 重複読み注釈の除去（`X（Y）` / `X、Y` で spoken一致ならY側を落とす）
     - 数字/英字/単位のカナ化（辞書を増殖させずに収束させるための決定論）
5) `audio_prep/script_sanitized.txt` と `final/a_text.txt` に materialize（下流は必ず final を参照）

### 7.2 VOICEVOX（strict・誤読ゼロの主線）

**アノテーションの流れ（VOICEVOX）**
1) 7.1 のB生成で `b_text` を確定
2) `audio_query` で VOICEVOX の実読 `kana` を取得
3) `MeCab(b_text)` の期待読みと突合（トリビアル差は許容）
4) 1件でもズレたら **停止**し、`audio_prep/reading_mismatches__*.json` を出す
5) 辞書/override を追加して **mismatch=0** になるまで繰り返す
6) mismatch=0 を満たしたら合成（wav/srt）へ進む

**Bテキストの理想（VOICEVOX）**
- 上記の突合で **mismatch=0 が機械的に証明できる**状態（=固定ルールとして運用できる）。
- 「曖昧語」はグローバル辞書に入れない（D-014）。
  - 例: `行ったり`（イッタリ/オコナッタリ等）→ `行ったり来たり` のようにフレーズ化、または `local_token_overrides.json`。

### 7.3 VOICEPEAK（strict・クラッシュ/テンポ対策込み）

VOICEPEAK は VOICEVOX のような `audio_query.kana` が無いため、**自動でmismatch=0を証明できない**。  
その代わり、B側を「誤読しにくい形」に寄せ、辞書同期/記録で再現性を担保する。

**アノテーションの流れ（VOICEPEAK）**
1) 7.1 のB生成で `b_text` を確定（数字/英字カナ化・重複読み注釈除去は同じ）
2) Voicepeak辞書ソースを合成側に取り込む（試行 / 安全語のみ）
   - repo SoT: `packages/audio_tts/data/voicepeak/dic.json`
   - local: `~/Library/Application Support/Dreamtonics/Voicepeak/settings/dic.json`
   - local GUI: `~/Library/Application Support/Dreamtonics/Voicepeak/settings/user.csv`
   - 注意: **曖昧語/1文字surfaceは取り込まない**（誤爆防止）
3) （オプション）テンポ改善: `comma_policy: "particles"` で助詞直後の `、` をB側だけ間引く（字幕は維持）
4) VOICEPEAK CLI へ入力（行/文で分割して安定化。CLI呼び出しはロックで直列化）
5) **サンプル再生で誤読チェック（必須）**（VOICEPEAKは機械的にmismatch=0を証明できないため）
   - `afplay workspaces/audio/final/{CH}/{NNN}/{CH}-{NNN}.wav`
   - 証跡: `workspaces/scripts/{CH}/{NNN}/audio_prep/voicepeak_manual_check.txt` に `OK/NG + 理由1行`

**Bテキストの理想（VOICEPEAK）**
- 生テキストに ASCII/数字を残さない（B側でカナ化済みにする）。
- 句読点は「息継ぎ」と「文の意味」に必要な最小限（読点過多はテンポ悪化の原因）。
- 同一入力なら同一B（再現性100%） + `a_text.txt` で常に証跡化。

---

## 8. 辞書運用（乱立防止 / 公式辞書へ反映）

### 8.1 ゴール（あなたの方針をSSOT化）
- 理想: **各エンジンの公式ユーザー辞書に登録されている状態**（ローカル試聴や手動TTSでも安定する）。
- ただし正本（レビュー可能/再現可能/配布可能）は **repo側の辞書** とし、`sync_*_user_dict` で公式辞書へ反映する（「公式辞書=配布先」運用）。

### 8.2 絶対NG（事故源）
- **曖昧語を“単語単体”で辞書登録しない**（例: `辛い`（ツライ/カライ）, `怒り`（イカリ/オコリ）, `十分`（ジュウブン/ジュップン）, `行ったり`（イッタリ/オコナッタリ））。
  - 代替: フレーズ化（例: `辛いカレー` / `辛い気持ち`） or `local_token_overrides.json`（位置指定）
- 公式辞書（VOICEVOX/VOICEPEAK）を **手で直接いじり続けない**（正本が逆転して再現不能になる）。
  - 例外（緊急で手直しした）→ **必ず repo辞書へ同内容を逆輸入**してから `sync` で整合させる。

### 8.3 追加先の決め方（最小で回す）
- **コードで解決できるもの**（数字/英字/重複読み注釈除去）は辞書を増やさない（B決定論に寄せる）。
- 辞書に入れるのは「読みが1つに確定できる」ものだけ（D-014）。
  - 全チャンネルで一意（確定語/昇格済・対話型AIエージェント判定） → `packages/audio_tts/data/global_knowledge_base.json`
  - そのチャンネルで一意 → `packages/audio_tts/data/reading_dict/CHxx.yaml`
  - その回だけ/文脈依存 → `audio_prep/local_token_overrides.json`（位置指定; 最終手段） / `audio_prep/local_reading_dict.json`（フレーズのみ）
- 昇格ルール（乱立防止・効果最大化）:
  - `local_token_overrides.json` は “最後の逃げ道”。同じ表記/同じ誤読が **別回でも再発**したら、まずフレーズ化できないか検討し、フレーズ化できる場合は `CHxx.yaml` へ昇格する。
  - `local_reading_dict.json` のフレーズが **同一CHで複数回**出るなら `CHxx.yaml` へ昇格（動画ローカルに残さない）。
  - `CHxx.yaml` の語が **他CHでも再発**し、読みが一意なら `global_knowledge_base.json` へ昇格（全CH一元化）。

### 8.5 Voicepeak手動辞書資産の扱い（再利用しつつ分離）
- Voicepeak の `user.csv`（人間がGUIで育てた辞書）は貴重だが、**VOICEVOXへは自動混入させない**（再現性が崩れるため）。
- 標準運用:
  1) `user.csv` の内容は “候補リスト” として扱う（安全語のみ抽出）
  2) **読みが一意で確定**できる語だけを `global_knowledge_base.json`（全CH共通）/ `CHxx.yaml`（CH固有）へ昇格
  3) VOICEVOX公式ユーザー辞書へは `--global-only` で同期（配布先として使う）

### 8.4 公式辞書への反映（手順）
- VOICEVOX（標準: グローバルのみ）: `PYTHONPATH=".:packages" python3 -m audio_tts.scripts.sync_voicevox_user_dict --global-only --overwrite`
- VOICEVOX（必要時: CHの補助語も同期）: `PYTHONPATH=".:packages" python3 -m audio_tts.scripts.sync_voicevox_user_dict --channel CHxx --overwrite`
- VOICEPEAK: `PYTHONPATH=".:packages" python3 -m audio_tts.scripts.sync_voicepeak_user_dict`（add-only）

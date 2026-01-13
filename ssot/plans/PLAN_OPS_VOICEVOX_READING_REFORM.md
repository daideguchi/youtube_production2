# OPS_VOICEVOX_READING_REFORM (SSOT)

> Plan metadata
> - Plan ID: **PLAN_OPS_VOICEVOX_READING_REFORM**
> - ステータス: Active
> - 担当/レビュー: Codex（最終更新者）
> - 対象範囲 (In Scope): `audio_tts` の VOICEVOX 読み誤り対策、SRT/Bテキスト生成経路
> - 非対象 (Out of Scope): 台本生成ロジックの刷新、投稿/動画編集 UI
- 最終更新日: 2025-12-11

> VOICEVOX 読み誤り対策の正本。旧 SSOT は `ssot_old/` に退避済みで、以後の更新・TODO 管理は本書のみで行う。

## 1. 目的と適用範囲
- 目的: VOICEVOX を用いた audio_tts パイプラインでの誤読・不自然抑揚を、LLM/MeCab/VOICEVOX の三者比較で検出・矯正する。
- 適用: `audio_tts` の Strict pipeline（例: `packages/audio_tts/scripts/run_tts.py` / `strict_orchestrator.run_strict_pipeline`）全般（A テキスト→ SRT → 音声/SRT 出力）。

## 1.1 最新方針（2025-12-11 再確定・実装済み）
- Ruby LLM の役割は「surface単位で VOICEVOX の読みが OK/NG を判定し、NG のときだけカナを返す」だけ。文を書き換えない。
- 1動画あたりの Ruby LLM は構造的に最大2コール / surface 最大40件（batch20×2）に固定。パラメータキャップではなく仕様。
- 送信対象は hazard レベルAのみ（英数字/数値/未知語/固有名詞/ハザード辞書）。レベルB（単なる block_diff）は `include_level_b=True` のときだけ。
- Voicevox の聴きやすさ揺れ（コオテエ/キョオ等）は正規化で trivial 判定し、LLM に送らない。禁止語（今日/今/助詞/1文字等）は全経路で除外。
- LLM I/O: 入力 items[{surface,mecab_kana,voicevox_kana,contexts,hazard_tags,positions}], 出力 items[{surface,decision=ok|ng|skip,correct_kana?}]; decision!=ng は無視。correct_kana はカタカナのみをローカル検証してから KanaPatch 化。
- KanaPatch は positions で全出現に適用し、accent_phrases で align。失敗時は長さクリップ＋fallback理由をログ。
- vocab LLM は本番パイプラインから切り離し（enable_vocab=False デフォルト）。辞書育成はオフラインバッチ前提にする。
- ログ: `tts_voicevox_reading.jsonl` に selected/adopted/rejected/calls、surface/mecab/voicevox/ruby、reason（hazard/trivial_skipped/banned/align_fallback 等）を記録し、集計でコール数≤2/件数≤40を確認する。
- **実行モード（固定）**（更新: 2026-01-10 / `ssot/DECISIONS.md:D-013`）:
  - TTS/Bテキスト系（`tts_*` / `voicevox_kana`）は **AIエージェント（Codex / pending運用）** を正とする（例: `./scripts/think.sh --tts -- <cmd>` または `LLM_EXEC_SLOT=3/4`）。
  - 注: ここで言う Codex は **codex exec（非対話CLI）ではない**（別物）。TTSを codex exec へ寄せない。
  - 失敗時は **LLM APIへ自動フォールバックしない**（停止して原因を残す）。
  - 比較/デバッグでAPI実行する場合のみ `LLM_EXEC_SLOT=0` を明示して実行する（通常運用で勝手に切り替えない）。
- **誤読ゼロ運用（グローバル確定）**:
  - `SKIP_TTS_READING=1`（読みLLM完全OFF）運用では、VOICEVOX の実読（`audio_query.kana`）と期待読み（MeCab+辞書/override）を突合する。
  - 1件でも不一致があれば **fail-fastで停止** し、`workspaces/scripts/{CH}/{VID}/audio_prep/reading_mismatches__*.json` を出力して修正の入口にする（誤読混入を禁止）。
  - trivial 差分（長音/表記ゆれ/区切り記号など）は正規化で無害化し、不一致に数えない（例: コーヒー/コヒー/コオヒイ）。
## 2. 現行実装サマリ（実コード優先）
- Aテキスト前処理: `factory_common.text_sanitizer.strip_meta_from_script`（Strict pipeline が使用）
- セグメント化: `packages/audio_tts/tts/strict_segmenter.py::strict_segmentation`
- 読み裁定（Twin-Engine + LLM batch）: `packages/audio_tts/tts/arbiter.py::resolve_readings_strict` → `packages/audio_tts/tts/auditor.py`
- 監査プロンプト枠: `tts/auditor.py` は hazard/辞書/トリビアル差分ゲート後の語彙をまとめて `tts_reading` にバッチ送信し、学習結果をチャンネル辞書に即反映する。【F:packages/audio_tts/tts/auditor.py†L8-L219】
- VOICEVOX 合成: `tts/synthesis.py::voicevox_synthesis(_chunks)` が `/audio_query`→`/synthesis` を実行し `accent_phrases` を保持可能。`apply_kana_patches` で moras を上書きでき、`voicevox_synthesis` は `patches`、`voicevox_synthesis_chunks` は `patches_by_block` を受け取って `/synthesis` に渡す。【F:packages/audio_tts/tts/synthesis.py】
- データクラス: `tts/reading_structs.py` にルビ・リスク・カナパッチの共通型（`RubyToken`, `RiskySpan`, `KanaPatch` 等）と LLM 呼び出しスケルトンが定義済み。【F:packages/audio_tts/tts/reading_structs.py†L1-L86】
- テスト: `packages/audio_tts/tests/test_apply_kana_patches.py` がパッチ適用の no-op/範囲内/範囲外/`correct_moras` 優先を検証。【F:packages/audio_tts/tests/test_apply_kana_patches.py†L1-L86】

## 3. 4レイヤー構成とコードマッピング
### 3.0 レイヤー0: 語彙キャッシュ / チャンネル辞書
- 追加: `packages/audio_tts/tts/reading_dict.py` を新設し、チャンネル単位の YAML 辞書をロード/マージする。`merge_channel_readings` で LLM 判定結果をキャッシュし、`arbiter.resolve_readings_strict` から `load_channel_reading_dict` を呼び出して WordDictionary に事前注入する。
- ロードタイミング: `strict_orchestrator.run_strict_pipeline` → `resolve_readings_strict` 呼び出し時に `channel` 引数を渡し、Voicevox 前の辞書適用に利用。
- 更新: LLM が返した `corrections` を `ReadingEntry` として YAML に書き戻す。アクセント情報は null 許容。
- ポリシー: 辞書はホワイトリスト（固有名詞/外来語/製品名）限定。1文字・助詞/助動詞・文脈依存語（今日/今/昨日/明日/今年/来年/去年など）は登録禁止で、ロード/保存/適用/LLM送信の全経路でフィルタする。

### 3.1 レイヤー1: LLM ルビ付け（現在は使用しない）
- 現状 Strict pipeline では **未配線**。LLMコスト削減と過剰介入防止のため、ルビ用LLM呼び出しは行わない。
- `reading_structs.call_llm_for_ruby` は将来の実験用に残置するが、運用フローでは呼び出さない。

### 3.2 レイヤー2: 危険箇所スコアリング
- 挿入位置: ルビ取得後、SRT 確定前に危険候補抽出。
- 実装案: `collect_risky_candidates(tokens, ruby_info, hazard_dict) -> list[RiskySpan]` を `packages/audio_tts/tts/auditor.py`（`arbiter.resolve_readings_strict` 経由）から呼ぶ。MeCab 複数読み/未知語フラグ + hazard 辞書を統合し `risk_score` を設定。

### 3.3 レイヤー3: VOICEVOX audio_query 差分評価
- 挿入位置: Twin-Engine 監査（`audit_blocks` 呼び出し）直前で `/audio_query` 結果を用いて差分比較。
- 実装案: `align_moras_with_tokens(accent_phrases, tokens, ruby_info) -> list[Tuple[RubyToken, List[str]]]` でモーラ列とトークンを対応付け、`evaluate_reading_diffs(aligned, llm_judger_fn=None) -> list[RiskySpan]` で LLM 判定を行う。

### 3.4 レイヤー4: 確定誤読のカナ強制
- 挿入位置: `voicevox_synthesis` / `voicevox_synthesis_chunks` 内、`/synthesis` 呼び出し直前にブロック単位の `KanaPatch` を適用。
- 実装: `apply_kana_patches(accent_phrases, patches)` を呼び、`correct_moras` があればモーラ単位、無い場合は 1文字≈1モーラで上書き。

## 4. インタフェース & データ構造（実コード）
- `RubyToken`: `surface, reading_hira, reading_kana_candidates?, token_index, line_id, char_range`。【F:packages/audio_tts/tts/reading_structs.py†L13-L22】
- `RubyLine`: `line_id, text, tokens`。【F:packages/audio_tts/tts/reading_structs.py†L25-L28】
- `RubyInfo`: `lines, raw_llm_payload?`。【F:packages/audio_tts/tts/reading_structs.py†L31-L34】
- `RiskySpan`: `line_id, token_index, risk_score, reason, surface, mora_range?`。hazard・差分理由を保持し語彙バッチ判定に渡す。【F:packages/audio_tts/tts/reading_structs.py†L36-L43】
- `KanaPatch`: `block_id, token_index, mora_range, correct_kana, correct_moras?`。`correct_moras` があれば優先。【F:packages/audio_tts/tts/reading_structs.py†L43-L62】
- LLM 呼び出し: `call_llm_for_ruby(lines: list[RubyLine]) -> RubyInfo`（チャンク入力 + payload 保存）。【F:packages/audio_tts/tts/reading_structs.py†L43-L55】
- アラインメント: `align_moras_with_tokens(accent_phrases, tokens) -> list[Tuple[RubyToken, List[str]]]`（差分評価前段）。【F:packages/audio_tts/tts/reading_structs.py†L58-L64】
- 差分評価: `evaluate_reading_diffs(aligned, llm_judger_fn=None) -> list[RiskySpan]`（LLM 判定差し替え可）。【F:packages/audio_tts/tts/reading_structs.py†L66-L77】

## 5. カナパッチ適用仕様
- 入力: `/audio_query` 返却の `accent_phrases`（1ブロック分）と、そのブロック用 `patches`。
- 実装: `synthesis.apply_kana_patches` が deep copy した `accent_phrases` を平坦化し、`correct_moras` → `correct_kana` の順で `moras[].text` を上書き。`correct_moras` が無い場合は 1文字≈1モーラの暫定置換。【F:packages/audio_tts/tts/synthesis.py†L120-L218】
- テスト: `packages/audio_tts/tests/test_apply_kana_patches.py` で no-op / 範囲内 / 範囲外 / `correct_moras` 優先を担保。追加パッチはこのテストを更新すること。【F:packages/audio_tts/tests/test_apply_kana_patches.py†L1-L86】
- TODO: multi-character モーラの安全適用（`correct_moras` 必須化）と `/synthesis` 直前のパッチ適用位置の配線（完了）。

## 6. ログ / hazard 辞書 / アクセント付きカナ再利用
- ログ: `workspaces/logs/tts_voicevox_reading.jsonl` を新設し、`{timestamp, channel, video, block_id, surface, llm_ruby, vv_moras, mecab_reading, decision, hazard_score}` を追記。LLM 評価 payload は `raw_llm_payload` として保持。実行プロファイルでは `audit_blocks_marked`（Twin-Engineで監査候補になったブロック数）と `risky_terms`（語彙バッチに実際に送った語数）、`tts_reading_calls`（実リクエスト回数）を JSONL に蓄積し、History 集計の根拠とする。
- hazard 辞書: `data/hazard_readings.yaml` をキャッシュ用途で管理し、`term, score, notes, last_seen` を保持。ログから週次集計で更新し、レイヤー1/3の優先度付けに利用。
- アクセント付きカナ学習: `/audio_query` の `accent_phrases[].moras` と LLM ルビを JSONL に蓄積し、将来の「漢字仮名交じり→アクセント付きカナ」モデルの教師データとする。文脈（前後文）とアクセント句境界/ピッチをそのまま保存。
- プロファイル計測: `packages/audio_tts/tts/strict_orchestrator.py::run_strict_pipeline` にレイヤー別タイマーを追加し、`TTS_PROFILE channel=...` ログと JSONL (`workspaces/logs/tts_voicevox_reading.jsonl`) に `{layer_times, tts_reading_calls, risky_terms}` を記録する。
- コスト上限: LLM 読み裁定はデフォルト calls<=3, vocab_terms<=120 に制限し、超過時は `budget_exceeded=true` をログ出力のうえ辞書/Voicevox優先で続行する（LLM追撃なし）。理由は `budget_exceeded:<stage>` 形式で JSONL に残す。

## 7. TODO / 実装ステータス（唯一の進行管理表）
- [x] `call_llm_for_ruby` 実装（チャンク入力 + RubyInfo 出力 + payload 保存）。
- [x] レイヤー2候補抽出: MeCab 複数読み/未知語 + hazard 辞書を組み合わせ `RiskySpan` を生成し、auditor で `collect_risky_candidates` によるゲートを有効化。
- [x] `/audio_query` 差分: `align_moras_with_tokens` + `evaluate_reading_diffs` を暫定実装し、代表例（怒り方/方が/辛い）でモーラ差分検証。
- [x] `apply_kana_patches` の multi-mora 安全化 + `/synthesis` 直前配線（ブロック別パッチを受け取る引数追加）。
- [x] ログ・hazard 辞書の JSONL/YAML ひな形配置と集計スクリプト追加（`scripts/`）。
- [x] HISTORY への進捗追記（`ssot_old` ではなく本 SSOT と `workspaces/logs/`/`data/` を正とする）。
- [x] レイヤー0導入: チャンネル辞書 (YAML) のロードと LLM 判定結果の書き戻し。`reading_dict.py` 追加。
- [x] 計測追加:（Legacy では）`run_tts_pipeline` にレイヤー別タイミングログと JSONL 追記、リスク件数/LLM 呼び出し回数の計測スケルトンを整備。
- [x] レイヤー3語彙バッチ化: `audit_blocks` で hazard/辞書/トリビアル通過語彙のみを抽出し、最大40語ずつ `tts_reading` にバッチ送信。返却読みはチャンネル辞書と学習辞書へ即書き戻し、`tts_reading_calls` に実リクエスト数を記録。
- [x] コスト/禁止語ガード: 辞書・学習・LLM送信の全経路で禁止語をフィルタし、LLM呼び出しを calls<=3 / vocab<=120 の上限で打ち切るフォールバックを導入。
- [ ] TTS三段導線（annotate→text_prepare→reading→SSML）を strict_orchestrator/strict_synthesizer に接続 | ⏳ llm_adapter 側は router 化済み。実配線は他エージェント対応中のため本タスクでは触らない。
- [ ] E2E スモーク（RUN_E2E_SMOKE=1 で実行可否切替） | ⏳ プレースホルダを tests に追加済み。実パイプラインは未実行。

## 8. 運用メモ
- 本書が唯一の更新ソース。旧 `ssot_old/` 配下やルート stub には追記しない。
- 追加メモや補助スクリプトを作成する場合も本書にリンクを張り、二重管理を避ける。
- 実装は既存パイプラインを壊さないよう段階的に差し込む（MeCab ドラフト→ルビ→差分→パッチの順）。

## 9. History
- 2025-12-10 (Codex): `synthesis.py` に `apply_kana_patches` を統合し、`voicevox_synthesis` へのパッチ適用ロジックを実装。`consonant` リセット処理を追加し、テスト (`packages/audio_tts/tests/test_apply_kana_patches.py`) を更新してパスさせた。
- 2025-12-10 (Codex): `apply_kana_patches` 実装およびテスト (`packages/audio_tts/tests/test_apply_kana_patches.py`) 追加。`strict_synthesizer.py` への配線完了。Layer 4 実装完了。
- 2025-12-10 (Codex): `mecab_tokenizer.py` の辞書スキャン深度修正、`arbiter.py` への「怒り」誤読防止ルール追加。
- 2025-12-11 (Codex): チャンネル辞書 (`reading_dict.py`) を追加し、Arbiter で LLM 裁定結果を YAML にキャッシュする経路を実装。（Legacy では）`run_tts_pipeline` にレイヤー別プロファイルログを出力する計測を追加し、辞書/ハザードのユーティリティ (`risk_utils.py`) とユニットテストを拡充。
- 2025-12-11 (Codex): auditor 経路にレイヤー2ゲートを配線。hazard 辞書/チャンネル辞書/トリビアル差分フィルタを通過したブロックだけを LLM (`tts_reading`) に送り、実際の LLM 呼び出し回数を JSONL に反映できるよう orchestration を更新。
- 2025-12-11 (Codex): レイヤー3を語彙単位バッチ監査に刷新。`RiskySpan.surface` を追加し、ブロック内の危険語彙をまとめて LLM adjudicator に送り、得られた読みを全ブロックへ一括適用。新規テストで語彙グルーピングと上限サンプリングを確認。プロファイルログの `risky_terms` を「実際に LLM へ送った語数」に変更し、`audit_blocks_marked` と分離して履歴の妥当性を担保。

---
- 最終更新: 2025-12-11 / 担当: Codex

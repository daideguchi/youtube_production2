# OPS_VOICEVOX_READING_REFORM (SSOT)

> Plan metadata
> - Plan ID: **PLAN_OPS_VOICEVOX_READING_REFORM**
> - ステータス: Active
> - 担当/レビュー: Codex（最終更新者）
> - 対象範囲 (In Scope): `audio_tts_v2` の VOICEVOX 読み誤り対策、SRT/Bテキスト生成経路
> - 非対象 (Out of Scope): 台本生成ロジックの刷新、投稿/動画編集 UI
> - 最終更新日: 2025-12-10

> VOICEVOX 読み誤り対策の正本。旧 SSOT は `ssot_old/` に退避済みで、以後の更新・TODO 管理は本書のみで行う。

## 1. 目的と適用範囲
- 目的: VOICEVOX を用いた audio_tts_v2 パイプラインでの誤読・不自然抑揚を、LLM/MeCab/VOICEVOX の三者比較で検出・矯正する。
- 適用: `audio_tts_v2` の `run_tts_pipeline` 実行経路全般（A テキスト→ SRT → B テキスト→音声/SRT 出力）。

## 2. 現行実装サマリ（実コード優先）
- Aテキスト前処理〜トークン化: `tts/orchestrator.py::run_tts_pipeline` で Markdown を保持したまま前処理→MeCab トークナイズを実行し `tokens` を生成。【F:audio_tts_v2/tts/orchestrator.py†L137-L205】
- Bテキスト初期生成: SRT ブロックごとに MeCab のドラフト読み (`generate_draft_readings`) を付与し `b_text` を保持。【F:audio_tts_v2/tts/orchestrator.py†L205-L273】
- Twin-Engine 監査: VOICEVOX `/audio_query` 由来の `voicevox_kana` と MeCab 読みを比較し、一致しないブロックへ `audit_needed` を設定。【F:audio_tts_v2/tts/orchestrator.py†L273-L332】
- 監査プロンプト枠: `tts/auditor.py` が LLM 判定用の枠を持ち、今後ルビ/差分比較に拡張予定。【F:audio_tts_v2/tts/auditor.py†L1-L115】
- VOICEVOX 合成: `tts/synthesis.py::voicevox_synthesis(_chunks)` が `/audio_query`→`/synthesis` を実行し `accent_phrases` を保持可能。`apply_kana_patches` で moras を上書きでき、`voicevox_synthesis` は `patches`、`voicevox_synthesis_chunks` は `patches_by_block` を受け取って `/synthesis` に渡す。【F:audio_tts_v2/tts/synthesis.py】
- データクラス: `tts/reading_structs.py` にルビ・リスク・カナパッチの共通型（`RubyToken`, `RiskySpan`, `KanaPatch` 等）と LLM 呼び出しスケルトンが定義済み。【F:audio_tts_v2/tts/reading_structs.py†L1-L86】
- テスト: `audio_tts_v2/tests/test_apply_kana_patches.py` がパッチ適用の no-op/範囲内/範囲外/`correct_moras` 優先を検証。【F:audio_tts_v2/tests/test_apply_kana_patches.py†L1-L86】

## 3. 4レイヤー構成とコードマッピング
### 3.1 レイヤー1: LLM ルビ付け
- 挿入位置: `run_tts_pipeline` のトークン生成直後。
- 実装: `reading_structs.call_llm_for_ruby(tokens: list[RubyToken] | list[dict], lines: list[str]) -> RubyInfo` を実装し、`RubyInfo` を `srt_blocks` へ付与。`raw_llm_payload` に生レスポンスを保持。

### 3.2 レイヤー2: 危険箇所スコアリング
- 挿入位置: ルビ取得後、SRT 確定前に危険候補抽出。
- 実装案: `collect_risky_candidates(tokens, ruby_info, hazard_dict) -> list[RiskySpan]` を orchestrator から呼ぶ。MeCab 複数読み/未知語フラグ + hazard 辞書を統合し `risk_score` を設定。

### 3.3 レイヤー3: VOICEVOX audio_query 差分評価
- 挿入位置: Twin-Engine 監査（`audit_blocks` 呼び出し）直前で `/audio_query` 結果を用いて差分比較。
- 実装案: `align_moras_with_tokens(accent_phrases, tokens, ruby_info) -> list[Tuple[RubyToken, List[str]]]` でモーラ列とトークンを対応付け、`evaluate_reading_diffs(aligned, llm_judger_fn=None) -> list[RiskySpan]` で LLM 判定を行う。

### 3.4 レイヤー4: 確定誤読のカナ強制
- 挿入位置: `voicevox_synthesis` / `voicevox_synthesis_chunks` 内、`/synthesis` 呼び出し直前にブロック単位の `KanaPatch` を適用。
- 実装: `apply_kana_patches(accent_phrases, patches)` を呼び、`correct_moras` があればモーラ単位、無い場合は 1文字≈1モーラで上書き。

## 4. インタフェース & データ構造（実コード）
- `RubyToken`: `surface, reading_hira, reading_kana_candidates?, token_index, line_id, char_range`。【F:audio_tts_v2/tts/reading_structs.py†L13-L22】
- `RubyLine`: `line_id, text, tokens`。【F:audio_tts_v2/tts/reading_structs.py†L25-L28】
- `RubyInfo`: `lines, raw_llm_payload?`。【F:audio_tts_v2/tts/reading_structs.py†L31-L34】
- `RiskySpan`: `line_id, token_index, risk_score, reason`（必要に応じ mora_range 拡張）。【F:audio_tts_v2/tts/reading_structs.py†L36-L40】
- `KanaPatch`: `block_id, token_index, mora_range, correct_kana, correct_moras?`。`correct_moras` があれば優先。【F:audio_tts_v2/tts/reading_structs.py†L43-L62】
- LLM 呼び出し: `call_llm_for_ruby(lines: list[RubyLine]) -> RubyInfo`（チャンク入力 + payload 保存）。【F:audio_tts_v2/tts/reading_structs.py†L43-L55】
- アラインメント: `align_moras_with_tokens(accent_phrases, tokens) -> list[Tuple[RubyToken, List[str]]]`（差分評価前段）。【F:audio_tts_v2/tts/reading_structs.py†L58-L64】
- 差分評価: `evaluate_reading_diffs(aligned, llm_judger_fn=None) -> list[RiskySpan]`（LLM 判定差し替え可）。【F:audio_tts_v2/tts/reading_structs.py†L66-L77】

## 5. カナパッチ適用仕様
- 入力: `/audio_query` 返却の `accent_phrases`（1ブロック分）と、そのブロック用 `patches`。
- 実装: `synthesis.apply_kana_patches` が deep copy した `accent_phrases` を平坦化し、`correct_moras` → `correct_kana` の順で `moras[].text` を上書き。`correct_moras` が無い場合は 1文字≈1モーラの暫定置換。【F:audio_tts_v2/tts/synthesis.py†L120-L218】
- テスト: `audio_tts_v2/tests/test_apply_kana_patches.py` で no-op / 範囲内 / 範囲外 / `correct_moras` 優先を担保。追加パッチはこのテストを更新すること。【F:audio_tts_v2/tests/test_apply_kana_patches.py†L1-L86】
- TODO: multi-character モーラの安全適用（`correct_moras` 必須化）と `/synthesis` 直前のパッチ適用位置の配線（完了）。

## 6. ログ / hazard 辞書 / アクセント付きカナ再利用
- ログ: `logs/tts_voicevox_reading.jsonl` を新設し、`{timestamp, channel, video, block_id, surface, llm_ruby, vv_moras, mecab_reading, decision, hazard_score}` を追記。LLM 評価 payload は `raw_llm_payload` として保持。
- hazard 辞書: `data/hazard_readings.yaml` をキャッシュ用途で管理し、`term, score, notes, last_seen` を保持。ログから週次集計で更新し、レイヤー1/3の優先度付けに利用。
- アクセント付きカナ学習: `/audio_query` の `accent_phrases[].moras` と LLM ルビを JSONL に蓄積し、将来の「漢字仮名交じり→アクセント付きカナ」モデルの教師データとする。文脈（前後文）とアクセント句境界/ピッチをそのまま保存。

## 7. TODO / 実装ステータス（唯一の進行管理表）
- [x] `call_llm_for_ruby` 実装（チャンク入力 + RubyInfo 出力 + payload 保存）。
- [ ] レイヤー2候補抽出: MeCab 複数読み/未知語 + hazard 辞書を組み合わせ `RiskySpan` を生成。
- [x] `/audio_query` 差分: `align_moras_with_tokens` + `evaluate_reading_diffs` を暫定実装し、代表例（怒り方/方が/辛い）でモーラ差分検証。
- [x] `apply_kana_patches` の multi-mora 安全化 + `/synthesis` 直前配線（ブロック別パッチを受け取る引数追加）。
- [x] ログ・hazard 辞書の JSONL/YAML ひな形配置と集計スクリプト追加（`scripts/`）。
- [x] HISTORY への進捗追記（`ssot_old` ではなく本 SSOT と `logs/`/`data/` を正とする）。

## 8. 運用メモ
- 本書が唯一の更新ソース。旧 `ssot_old/` 配下やルート stub には追記しない。
- 追加メモや補助スクリプトを作成する場合も本書にリンクを張り、二重管理を避ける。
- 実装は既存パイプラインを壊さないよう段階的に差し込む（MeCab ドラフト→ルビ→差分→パッチの順）。

## 9. History
- 2025-12-10 (Codex): `synthesis.py` に `apply_kana_patches` を統合し、`voicevox_synthesis` へのパッチ適用ロジックを実装。`consonant` リセット処理を追加し、テスト (`audio_tts_v2/tests/test_apply_kana_patches.py`) を更新してパスさせた。
- 2025-12-10 (Codex): `apply_kana_patches` 実装およびテスト (`audio_tts_v2/tests/test_apply_kana_patches.py`) 追加。`strict_synthesizer.py` への配線完了。Layer 4 実装完了。
- 2025-12-10 (Codex): `mecab_tokenizer.py` の辞書スキャン深度修正、`arbiter.py` への「怒り」誤読防止ルール追加。

---
- 最終更新: 2025-12-10 / 担当: Codex

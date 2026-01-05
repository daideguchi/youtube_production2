# Gaps Register（SSOT ↔ 実装の乖離台帳）

この台帳は「SSOTに書いてあること」と「実装（実際に動く入口/保存先）」の乖離を、**根拠付き**で列挙する。
（方針: ここでは観測事実と判断ポイントを固め、確定したら SSOT → 実装 の順で修正する）

凡例:
- 重要度: P0=事故/データ破壊級, P1=運用迷子/コスト事故, P2=ドキュメント齟齬, P3=軽微
- 状態: ✅=確定 / 🟡=要追加確認

---

## GAP-001（P0 ✅）redoフラグの正本は status.json（SSOT/実装一致・クローズ）

### 現状（SSOT/実装/運用）
- SSOT（確定）: `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md` にて **redo正本は `workspaces/scripts/{CH}/{NNN}/status.json: metadata.redo_*`** と明記済み（CSVには置かない）。
- UI（集計）: `ssot/ops/OPS_UI_WIRING.md` の通り、Planning CSV を母集団にして `status.json metadata.redo_*` を上書き表示する（status無は default true、投稿済みは false）。
- 実装（保存先）: `PATCH /api/channels/{CH}/videos/{NNN}/redo` が `status.json metadata.redo_*` を更新する（`apps/ui-backend/backend/main.py`）。

### 判断
- ここは乖離ではなく、**SSOT/実装は一致**しているためクローズ。
- 運用ルール: Planning CSV に redo を書く運用は行わない（母集団/識別子の正本に限定）。

---

## GAP-002（P1 ✅）LLM品質ゲートの収束上限（SSOT/実装一致・クローズ）

### 現状（SSOT/実装）
- SSOT: `ssot/ops/OPS_A_TEXT_LLM_QUALITY_GATE.md`「1.8 収束の上限」にて **`codex_exec` の回は default/hard cap を 5** に引き上げる旨を明記済み。
- 実装: `packages/script_pipeline/runner.py` の `script_validation` LLM quality gate v2 は、
  - `codex_exec` の場合: default/hard cap = 5
  - それ以外: default/hard cap = 3

### 判断
- SSOT/実装は一致しているためクローズ。
- 運用調整は SSOT 記載の env（例: `SCRIPT_VALIDATION_LLM_MAX_ROUNDS`）で行う。

---

## GAP-003（P2 ✅）Publish の一時DL/認証表記（SSOT/実装一致・クローズ）

### 現状（SSOT/実装）
- SSOT: `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`（Phase G Outputs）にて **一時DL=system temp（`tempfile.mkstemp(prefix=\"yt_upload_\", suffix=\".bin\")`）** と明記済み。
- SSOT: OAuth 変数も `YT_OAUTH_TOKEN_PATH` / `YT_OAUTH_CLIENT_PATH` に整理済み（token必須 / clientは初回セットアップ用）。
- 実装: `scripts/youtube_publisher/publish_from_sheet.py` は system temp に DL し、Sheet を更新する（dry-run/--run）。

### 判断
- SSOT/実装は一致しているためクローズ。
- 残課題（乖離ではなく改善）: system temp の `yt_upload_*.bin` を upload 後に削除するか/保持するか、運用方針を決める（保持するなら置き場とログを SSOT で固定）。

---

## GAP-004（P2 ✅）`run_pipeline --engine capcut` は stub（SSOT/実装一致・クローズ）

### 現状（SSOT/実装）
- SSOT: `ssot/ops/OPS_ENTRYPOINTS_INDEX.md` にて **`run_pipeline --engine capcut` は stub（非本番）** と明記済み。主線は `auto_capcut_run` + `capcut_bulk_insert`。
- 実装: CapCut draft の本番生成は `packages/video_pipeline/tools/capcut_bulk_insert.py`（テンプレ複製 + 画像/字幕/帯注入 + validation）。

### 判断
- SSOT/実装は一致しているためクローズ。
- 残課題（乖離ではなく事故防止）: stub を誤用しにくくする（CLIガード/明示的な “experimental” フラグ化/廃止）。これは `ssot/ops/OPS_OPEN_QUESTIONS.md` 側で扱う。

---

## GAP-005（P1 ✅）`script_enhancement` stage が no-op（stage定義↔実装の内部不整合）

### 旧観測（問題）
- `packages/script_pipeline/stages.yaml` に `script_enhancement`（LLM task `script_chapter_review`）が定義されていたが、`outputs: []` だった。
- `packages/script_pipeline/runner.py:_run_llm()` は **outputs が空かつ output_override が無い場合は実行しない**ため、実質 no-op になっていた。
- その結果、stage が **何もせず completed 扱い**になり、UI/運用が誤認しやすかった。

### 影響
- 「改善パスが走った」と誤認し、品質/やり直し判断が崩れる（コスト事故）。
- SSOT=UI の“1ステップ=1処理”の一致が崩れる。

### 判断
- `ssot/DECISIONS.md:D-011` を確定し、`script_enhancement` を主線から除外してクローズ（no-op解消）。

### 現状（解消後）
- `script_enhancement` は主線の stage 定義から除外（no-op排除）。
- 既存の `status.json` に `script_enhancement` が残っていても、主線では実行されない（互換のため放置可）。

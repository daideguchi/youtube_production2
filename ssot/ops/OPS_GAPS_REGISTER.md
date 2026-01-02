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

## GAP-003（P2 ✅）Publish（Sheet→Drive→YouTube）の一時DLパス/トークン表記がSSOTとズレる

### SSOT主張（例）
- `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`（Publish Outputs）に「一時DL: ローカル tmp/yt_upload_*.bin」と記載がある。

### 実装の現実
- `scripts/youtube_publisher/publish_from_sheet.py`:
  - 一時DLは `tempfile.mkstemp(prefix="yt_upload_", suffix=".bin")`（=OSの temp dir。repo の `tmp/` ではない）
- `YT_OAUTH_CLIENT_PATH` は **publish_from_sheet.py では使用していない**（tokenのみ）。クライアントsecretは `scripts/youtube_publisher/oauth_setup.py` が使用する。
- トークン/クライアントの“代表パス”は README/SSOT/環境変数で表記揺れがある（`YT_OAUTH_TOKEN_PATH` などに収束させる設計だが、文書側が統一されていない）。
- publish_from_sheet は一時DLした `yt_upload_*.bin` を **自動削除しない**（実行回数に応じてOS tempが肥大化し得る）。

### 影響
- cleanup/監査の前提がズレる（「repo/tmp を消す」が意味を持たない）。
- 初回セットアップで迷子になりやすい。
- OS temp の肥大化・容量逼迫（長期運用で効く）。

### 判断ポイント（要意思決定）
- SSOTを「system temp」に直すか、実装を `workspaces/tmp/` 等へ寄せるか。
- 一時DLファイルを「アップロード後に削除」するか、「保持して監査/再送に使う」か（保持するなら置き場とログを決める）。

---

## GAP-004（P2 🟡）video_pipeline の `run_pipeline --engine capcut` が stub で、主線は capcut_bulk_insert

### 観測
- `packages/video_pipeline/src/srt2images/engines/capcut_engine.py` は「stub draft」生成（README.txt + draft_meta.json + draft_content.json）。
- 実運用の CapCut draft は `video_pipeline.tools.capcut_bulk_insert.py`（テンプレ複製 + 画像/字幕/帯注入 + style正規化）が担う。
- SSOT上の正規入口は `auto_capcut_run.py` + `capcut_bulk_insert.py` なので、設計としては問題ないが、`run_pipeline --engine capcut` が残っていると誤用が起きる余地がある。

### 影響
- 新規エージェントが `--engine capcut` を使って「draftができた」と誤認するリスク。

### 判断ポイント（要意思決定）
- SSOTに「run_pipelineのcapcutはstub/非推奨」を明記するか、CLI側でガード/廃止するか。

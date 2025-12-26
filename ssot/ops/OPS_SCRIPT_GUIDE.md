# OPS_SCRIPT_GUIDE — 台本パイプライン運用手順（正本/入口/やり直し）

この文書は「台本を作る/直す/やり直す」の運用手順を **CWD非依存・パスSSOT前提** で固定する。  
処理フロー/I/Oの正本は `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`。

台本量産ロジック（単一SSOT）は `ssot/ops/OPS_SCRIPT_PIPELINE_SSOT.md`（本書は運用手順の詳細）。

全チャンネル共通の読み台本ルール（Aテキスト品質の下限）は `ssot/ops/OPS_A_TEXT_GLOBAL_RULES.md` が正本。

推奨実行（共通）:
- **必ず** `./scripts/with_ytm_env.sh .venv/bin/python ...` を使う（.envロード + venv依存を固定）。
  - 例: `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/script_runbook.py new --channel CH10 --video 008`

---

## 0. SoT（正本）

- 企画SoT: `workspaces/planning/channels/CHxx.csv`
- 台本SoT: `workspaces/scripts/{CH}/{NNN}/status.json`
- 台本本文（Aテキスト / 入力の正）:
  - 正本: `workspaces/scripts/{CH}/{NNN}/content/assembled_human.md`（存在する場合）
  - ミラー: `workspaces/scripts/{CH}/{NNN}/content/assembled.md`（正本と同内容に揃える）

---

## 1. 入口（絶対固定 / 4パターン）

日常運用で叩く入口は **これだけ**。  
モード（new/redo-full/resume/rewrite）の正本は `ssot/ops/OPS_SCRIPT_FACTORY_MODES.md`。

- `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/script_runbook.py <MODE> ...`

代表例:
- 新規で1から:
  - `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/script_runbook.py new --channel CH10 --video 008`
- 最初から完全にやり直す:
  - `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/script_runbook.py redo-full --channel CH07 --from 019 --to 030`
- 途中から再開:
  - `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/script_runbook.py resume --channel CH07 --video 019`
- リライト修正（ユーザー指示必須）:
  - `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/script_runbook.py rewrite --channel CH07 --video 019 --instruction \"言い回しをもっと理解しやすい表現に\"`

補助（検査だけ。再生成しない）:
- `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/script_runbook.py redo --channel CH07 --from 019 --to 030 --mode validate`

注意:
- `python -m script_pipeline.cli ...` は内部/詳細制御。入口を増やすと事故るので、通常は使わない（必要時のみ「付録A」を参照）。
- 超長尺（2〜3時間級）は例外運用（Marathon）。「付録B」を参照。

---

## 2. 出力（I/Oの目安）

- `workspaces/scripts/{CH}/{NNN}/content/`
  - `assembled.md`（最終台本）
  - `assembled_with_quotes.md` など（運用で採用ルールを固定する）
- `workspaces/scripts/{CH}/{NNN}/logs/`
  - `{stage}_prompt.txt`, `{stage}_response.json`（証跡）

---

## 3. 状態確認・整合

- 基本: 入口固定（`scripts/ops/script_runbook.py ...`）の出力JSONを見て判断する（`ok / pending / note`）。
- 内部CLI（必要時のみ。付録A）:
  - `./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli status --channel CH06 --video 033`
  - `./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli validate --channel CH06 --video 033`
  - `./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli reconcile --channel CH06 --video 033`（既存出力からstatusを補正）

### 3.0 UI（Episode Studio）での復旧

UI側でも「詰まったらまずここ」を固定する。

- `Episode Studio` → `パイプライン（ステージ）` から実行:
  - `Reconcile（status補正）`（API: `POST /api/channels/{ch}/videos/{video}/script-pipeline/reconcile`）
  - `script_validation 実行`（API: `POST /api/channels/{ch}/videos/{video}/script-pipeline/run/script_validation`）
- `script_validation` が NG の場合:
  - `status.json: stages.script_validation.details.error_codes / issues / fix_hints` を読み、`assembled_human.md`（なければ `assembled.md`）を修正してから再実行する。
  - 追加の品質ゲート（LLM Judge/Fixer）が有効な場合は、`content/analysis/quality_gate/` の judge/fix レポートも確認する（どこが不自然か／何を直せば良いかが残る）。

### 3.1 Script Validation（品質ゲート）

- 実行（推奨: 入口固定）:
  - `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/script_runbook.py resume --channel CHxx --video NNN`
- （内部CLI。必要時のみ）:
  - `./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli run --channel CHxx --video NNN --stage script_validation`
- NG時: `status.json: stages.script_validation.details.error_codes / issues` に理由が残る（UIにも表示される想定）。修正後に同じコマンドを再実行する。
- 判定基準（正本）:
  - `ssot/ops/OPS_A_TEXT_GLOBAL_RULES.md`（禁則・TTS事故防止の下限）
  - `ssot/ops/OPS_A_TEXT_LLM_QUALITY_GATE.md`（字数合格だけを禁止。LLM Judge→Fixer の2段階）

追加ゲート（意味整合）:
- 正本: `ssot/ops/OPS_SEMANTIC_ALIGNMENT.md`
- 既定では `verdict: major` のみ停止（ok/minor は合格）。minor/major は可能なら最小リライトを自動適用して収束させる。
- 厳密に止めたい場合は `SCRIPT_VALIDATION_SEMANTIC_ALIGNMENT_REQUIRE_OK=1`（ok 以外は停止。コスト優先なら `SCRIPT_VALIDATION_SEMANTIC_ALIGNMENT_AUTO_FIX_MINOR=0` も推奨）。
- 手動で直す場合（内部CLI。通常は不要）:
  - `./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli semantic-align --channel CHxx --video NNN`（チェックのみ）
  - `./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli semantic-align --channel CHxx --video NNN --apply --also-fix-minor`（最小リライト）

字数NG（短すぎ/長すぎ）への対処:
- 原則: reset→再生成（混入/水増しの副作用が最小）
- “軽い短尺補正” のみ許可する場合:
  - `./scripts/with_ytm_env.sh .venv/bin/python scripts/expand_a_text.py --channel CHxx --video NNN --mode run --hint "水増し禁止/現代の作り話禁止"`
  - 実行後に `script_validation` を再実行して通す（長尺2〜3h級はMarathon推奨）

---

## 4. やり直し（Redo / Reset）

### 4.1 企画側が更新された場合（CSV更新後）
原則:
- 企画CSVを直したら、台本は **reset→再生成** を基本にする（旧台本が残ると混乱源）。

コマンド（推奨: 入口固定）:
- `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/script_runbook.py redo-full --channel CHxx --from NNN --to NNN --wipe-research`

（内部CLI。必要時のみ）:
- `./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli reset --channel CHxx --video NNN --wipe-research`

### 4.2 人間が台本を直した場合
原則:
- `assembled_human.md`（正本）または `assembled.md`（ミラー）を更新したら、それ以降（音声/動画）は **必ず再生成** する。
- 安全のため、まず `script_validation` を再実行してから音声へ進む（UI保存時も `script_validation` が pending になるのが正）。

---

## 5. 禁止事項（破綻を防ぐ）

- パス直書き禁止（`packages/factory_common/paths.py` を使う）
- `status.json` を手で大改造しない（必要なら `reset/reconcile` を使う）
- `assembled.md` と別の入力で音声生成しない（例外はSSOTに残す）

### 5.1 台本本文に “メタ情報（出典/脚注/URL）” を混入させない

目的: 字幕/SRTや音声に **出典が表示・読み上げされる事故**を根絶する（迷わない/壊さない）。

- 禁止例（台本本文に入れない）:
  - `([戦国ヒストリー][13])` のような Markdown 参照リンク
  - `[...]` 内が数字の脚注（例: `[13]`）
  - URL（`https://...` / `www...`）
  - `Wikipedia/ウィキペディア` を “出典として” 直接書く表現（必要なら本文で自然な言い換えにする）
- 出典は本文ではなく `content/analysis/research/references.json` 等へ集約する（SoTは research 側）
- 既に混入してしまった場合:
  - まず台本（Aテキスト）を正に戻す（`scripts/sanitize_a_text.py` で退避→除去→同期）
  - その後に音声/TTSとCapCutを再生成して 1:1 を回復する

### 5.2 Aテキストの区切り記号は `---` のみ

目的: TTSで **意図した箇所だけ** ポーズを入れ、字幕/読み上げの事故を防ぐ。

- 許可: `---`（1行単独。話題転換/場面転換など文脈ベースで挿入）
- 禁止: `***` / `___` / `///` / `===` などの区切り記号（TTS分割の不自然さ・混乱源）
- 注意: `「」` と `（）` はTTSが不自然に途切れやすいので **多用しない**（詳細は `ssot/ops/OPS_A_TEXT_GLOBAL_RULES.md`）

---

## 付録A. 内部CLI（デバッグ/詳細制御。通常運用では使わない）

入口固定（`scripts/ops/script_runbook.py`）を守るため、ここは必要時だけに限定する。

- 状態:
  - `./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli status --channel CHxx --video NNN`
  - `./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli validate --channel CHxx --video NNN`
  - `./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli reconcile --channel CHxx --video NNN`
- 初期化（status.jsonが無いときだけ）:
  - `./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli init --channel CHxx --video NNN --title \"<title>\"`
- 実行:
  - `./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli next --channel CHxx --video NNN`
  - `./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli run-all --channel CHxx --video NNN`
  - `./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli run --channel CHxx --video NNN --stage script_outline`
- リセット（やり直しの強制。redo-fullの代替）:
  - `./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli reset --channel CHxx --video NNN --wipe-research`

---

## 付録B. 超長尺（2〜3時間級 / 全文LLM禁止: Marathon）

超長尺では、`script_validation` の **全文LLM Judge/Fix** がコンテキスト・コスト・部分改変事故で破綻しやすい。  
したがって「章分割→機械（非LLM）アセンブル」を前提にした Marathon を使う（詳細: `ssot/ops/OPS_LONGFORM_SCRIPT_SCALING.md`）。

- planのみ（設計だけ作る）:
  - `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/a_text_marathon_compose.py --channel CHxx --video NNN --duration-minutes 120 --plan-only`
- dry-run（analysis/longform に生成、正本は触らない）:
  - `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/a_text_marathon_compose.py --channel CHxx --video NNN --duration-minutes 120`
- apply（canonical を上書き）:
  - `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/a_text_marathon_compose.py --channel CHxx --video NNN --duration-minutes 120 --apply`
- ブロック雛形（章の箱）を指定したい場合:
  - `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/a_text_marathon_compose.py --channel CHxx --video NNN --duration-minutes 120 --block-template personal_benefit_v1 --apply`
  - 正本: `configs/longform_block_templates.json`（templates / channel_overrides）

確認（推奨）:
- 機械lint（非LLM。禁則/反復/まとめ重複）:
  - `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/a_text_lint.py --channel CHxx --video NNN --write-latest`

注意:
- Marathon は `content/analysis/longform/` に plan/候補/検証ログを残す（やり直し・原因追跡用）。
- 超長尺で `script_validation` を回す場合は **全文LLMを無効化**して機械チェックだけ使う:
  - `SCRIPT_VALIDATION_LLM_QUALITY_GATE=0 ./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli run --channel CHxx --video NNN --stage script_validation`
  - 追加の安全弁（runner側）: `SCRIPT_VALIDATION_LLM_MAX_A_TEXT_CHARS`（default: `30000`）超過時は、全文LLMゲートが自動スキップされる（強制は `SCRIPT_VALIDATION_FORCE_LLM_GATE=1`）。

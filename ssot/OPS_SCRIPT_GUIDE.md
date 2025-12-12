# OPS_SCRIPT_GUIDE — 台本パイプライン運用手順（正本/入口/やり直し）

この文書は「台本を作る/直す/やり直す」の運用手順を **CWD非依存・パスSSOT前提** で固定する。  
処理フロー/I/Oの正本は `ssot/OPS_CONFIRMED_PIPELINE_FLOW.md`。

---

## 0. SoT（正本）

- 企画SoT: `progress/channels/CHxx.csv`
- 台本SoT: `script_pipeline/data/{CH}/{NNN}/status.json`
- 台本本文（入力の正）: `script_pipeline/data/{CH}/{NNN}/content/assembled.md`

---

## 1. 入口（Entry points）

### 1.1 初期化（status.json が無い場合）
- `python -m script_pipeline.cli init --channel CH06 --video 033 --title "<title>"`

### 1.2 実行（ステージ指定）
- `python -m script_pipeline.cli run --channel CH06 --video 033 --stage script_outline`

### 1.3 実行（次のpendingを進める）
- `python -m script_pipeline.cli next --channel CH06 --video 033`
- `python -m script_pipeline.cli run-all --channel CH06 --video 033`

---

## 2. 出力（I/Oの目安）

- `script_pipeline/data/{CH}/{NNN}/content/`
  - `assembled.md`（最終台本）
  - `assembled_with_quotes.md` など（運用で採用ルールを固定する）
- `script_pipeline/data/{CH}/{NNN}/logs/`
  - `{stage}_prompt.txt`, `{stage}_response.json`（L3: 証跡）

---

## 3. 状態確認・整合

- `python -m script_pipeline.cli status --channel CH06 --video 033`
- `python -m script_pipeline.cli validate --channel CH06 --video 033`
- `python -m script_pipeline.cli reconcile --channel CH06 --video 033`（既存出力からstatusを補正）

---

## 4. やり直し（Redo / Reset）

### 4.1 企画側が更新された場合（CSV更新後）
原則:
- 企画CSVを直したら、台本は **reset→再生成** を基本にする（旧台本が残ると混乱源）。

コマンド:
- `python -m script_pipeline.cli reset --channel CH06 --video 033`
  - 追加で調査出力も消す場合: `--wipe-research`

### 4.2 人間が台本を直した場合
原則:
- `assembled.md` を更新したら、それ以降（音声/動画）は **必ず再生成** する。

---

## 5. 禁止事項（破綻を防ぐ）

- パス直書き禁止（`factory_common/paths.py` を使う）
- `status.json` を手で大改造しない（必要なら `reset/reconcile` を使う）
- `assembled.md` と別の入力で音声生成しない（例外はSSOTに残す）


# OPS_SCRIPT_GUIDE — 台本パイプライン運用手順（正本/入口/やり直し）

この文書は「台本を作る/直す/やり直す」の運用手順を **CWD非依存・パスSSOT前提** で固定する。  
処理フロー/I/Oの正本は `ssot/OPS_CONFIRMED_PIPELINE_FLOW.md`。

全チャンネル共通の読み台本ルール（Aテキスト品質の下限）は `ssot/OPS_A_TEXT_GLOBAL_RULES.md` が正本。

---

## 0. SoT（正本）

- 企画SoT: `workspaces/planning/channels/CHxx.csv`（互換: `progress/channels/CHxx.csv`）
- 台本SoT: `workspaces/scripts/{CH}/{NNN}/status.json`（互換: `script_pipeline/data/...`）
- 台本本文（Aテキスト / 入力の正）:
  - 正本: `workspaces/scripts/{CH}/{NNN}/content/assembled_human.md`（存在する場合）
  - フォールバック: `workspaces/scripts/{CH}/{NNN}/content/assembled.md`
  - 互換: `assembled.md` は **mirror**（`assembled_human.md` と一致させる）

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

- `workspaces/scripts/{CH}/{NNN}/content/`（互換: `script_pipeline/data/...`）
  - `assembled.md`（最終台本）
  - `assembled_with_quotes.md` など（運用で採用ルールを固定する）
- `workspaces/scripts/{CH}/{NNN}/logs/`（互換: `script_pipeline/data/...`）
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
- 注意: `「」` と `（）` はTTSが不自然に途切れやすいので **多用しない**（詳細は `ssot/OPS_A_TEXT_GLOBAL_RULES.md`）

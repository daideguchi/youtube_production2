# OPS_SCRIPT_PIPELINE_SSOT — 台本量産ロジックの正本（新規/やり直し/超長尺）

目的:
- 「台本が破綻する/水増しする/タイトルとズレる/同じ話を繰り返す」事故を、**手作業やリトライではなく仕組み**で止める。
- 新規作成とやり直し（Redo）を、低知能エージェントでも迷わず実行できる **確定フロー**にする。
- 2〜3時間級（超長尺）でも破綻しない運用を、**全文LLM禁止**を前提に固定する。

この文書は「台本パイプラインの単一SSOT（1枚）」である。詳細は必要時にリンク先へ降りるが、**迷ったら本書の手順を優先**する。

関連（詳細/分割SSOT）:
- 確定E2Eフロー（観測ベースの正本）: `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`
- 台本アーキテクチャ（構造で壊さない）: `ssot/ops/OPS_SCRIPT_GENERATION_ARCHITECTURE.md`
- 入力契約（L1/L2/L3）: `ssot/ops/OPS_SCRIPT_INPUT_CONTRACT.md`
- 運用手順（入口/やり直し）: `ssot/ops/OPS_SCRIPT_GUIDE.md`
- 品質ゲート（Judge→Fixer→必要ならRebuild）: `ssot/ops/OPS_A_TEXT_LLM_QUALITY_GATE.md`
- 超長尺（Marathon）設計: `ssot/ops/OPS_LONGFORM_SCRIPT_SCALING.md`
- 構成パターン（骨格/字数配分SSOT）: `ssot/ops/OPS_SCRIPT_PATTERNS.yaml`

---

## 0) SoT（正本）とI/O（迷子を止める固定）

SoT（正本）:
- Planning SoT（企画/進捗）: `workspaces/planning/channels/CHxx.csv`（互換: `progress/channels/CHxx.csv`）
- Script SoT（台本ステージ状態）: `workspaces/scripts/{CH}/{NNN}/status.json`（互換: `script_pipeline/data/{CH}/{NNN}/status.json`）
- 台本本文（Aテキスト）:
  - 正本: `workspaces/scripts/{CH}/{NNN}/content/assembled_human.md`（存在する場合）
  - フォールバック: `workspaces/scripts/{CH}/{NNN}/content/assembled.md`
  - 注意: `assembled.md` は mirror 扱い（最終的に `assembled_human.md` と一致させる）

証跡/ログ（L3; 参照はできるが正本ではない）:
- ステージごとの入出力: `workspaces/scripts/{CH}/{NNN}/logs/{stage}_prompt.txt`, `.../{stage}_response.json`
- 研究/判定ログ: `workspaces/scripts/{CH}/{NNN}/content/analysis/**`

---

## 1) 入力契約（L1/L2/L3）— 必須/任意/混入禁止

台本生成の品質は「プロンプトの長さ」より **入力の階層**で決まる。

### 1.1 L1（必須・最優先）
- 企画タイトル（Planning SoT）
- persona / channel_prompt（チャンネルの狙い・トーン）
- 構成パターン（骨格・字数配分）: `ssot/ops/OPS_SCRIPT_PATTERNS.yaml`
- 全チャンネル共通の禁則/書式: `ssot/ops/OPS_A_TEXT_GLOBAL_RULES.md`

### 1.2 L2（補助ヒント・あれば使う）
- CSVの補助メタ（企画意図/ターゲット/キーコンセプト等）

ルール:
- L2に矛盾が見えたら **捨ててL1で書く**（安全で安い）。
- 代表的な混線は `tag_mismatch`（タイトル先頭の `【...】` と要約先頭の `【...】` が不一致）。検出時は L2 を自動で落とすのが正しい。

### 1.3 L3（混入禁止）
- 旧台本断片、コピペ用自由文、途中生成の断片、人間向けメモ

理由:
- L3は高確率で「反復/別テーマ混入/作り話感」の汚染源になる。

---

## 2) 生成モード（標準/長尺/超長尺）— どれを使うか

基本方針:
- リトライ回数を増やして当てない。
- 「骨格（決定論）」→「本文（推論）」→「最小修正（推論）」の順で収束させる。

### 2.1 標準（主線）: script_pipeline（ステージ管理）
入口:
- `python -m script_pipeline.cli init --channel CHxx --video NNN --title \"<title>\"`
- `python -m script_pipeline.cli run-all --channel CHxx --video NNN`

出力（主なもの）:
- `content/outline.md`（アウトライン）
- `content/chapters/chapter_*.md`（章草稿）
- `content/assembled.md`（結合）
- `script_validation`（品質ゲート）

### 2.2 長尺（安定化）: セクション分割→組み上げ（Section Compose）
目的:
- 1撃長文より、セクション単位で「迷子/水増し/禁則」を潰して安定化する。

入口（推奨）:
- `python3 scripts/ops/a_text_section_compose.py --channel CHxx --video NNN`（dry-run）
- `python3 scripts/ops/a_text_section_compose.py --channel CHxx --video NNN --apply --run-validation`

ルール:
- セクション本文内に `---` を入れない（区切りは組み上げ工程だけ）
- NGは「そのセクションだけ」再生成（最大N回）

### 2.3 超長尺（2〜3時間級）: Marathon（全文LLM禁止）
結論:
- 超長尺は全文をLLMに渡す品質ゲート（Judge/Fix）が破綻しやすい。
- したがって「章設計→章ごと生成→決定論アセンブル→チャンク判定/差し替え」で収束させる。

入口（推奨）:
- `python3 scripts/ops/a_text_marathon_compose.py --channel CHxx --video NNN --duration-minutes 120 --plan-only`
- `python3 scripts/ops/a_text_marathon_compose.py --channel CHxx --video NNN --duration-minutes 120`（dry-run）
- `python3 scripts/ops/a_text_marathon_compose.py --channel CHxx --video NNN --duration-minutes 120 --apply`

注意:
- 超長尺で `script_validation` を回す場合は全文LLMを無効化して決定論チェックだけ使う:
  - `SCRIPT_VALIDATION_LLM_QUALITY_GATE=0 python -m script_pipeline.cli run --channel CHxx --video NNN --stage script_validation`

---

## 3) 新規作成フロー（確定）

1. Planning SoT を確認（タイトル/進捗/補助メタ）
2. `init`（status.json が無ければ）
3. `run-all`（または `next` で段階実行）
4. `script_validation` を通す（落ちたら Fix か redo）
5. 合格した台本だけ音声へ進む

最短コマンド:
- `python -m script_pipeline.cli init --channel CHxx --video NNN --title \"<title>\"`
- `python -m script_pipeline.cli run-all --channel CHxx --video NNN`
- `python -m script_pipeline.cli run --channel CHxx --video NNN --stage script_validation`

---

## 4) やり直し（Redo / Reset）フロー（確定）

Redo は「何を正本として残すか」を固定しないと、参照が混線して破綻する。

### 4.1 CSV（企画）が変わった
原則:
- reset→再生成（旧台本が残ると混乱源）。

コマンド:
- `python -m script_pipeline.cli reset --channel CHxx --video NNN`
  - 調査も消す: `--wipe-research`

### 4.2 人間が本文（assembled_human）を直した
原則:
- 以降（音声/動画）は必ず再生成。
- まず `script_validation` を再実行して品質を担保してから音声へ進む。

コマンド:
- `python -m script_pipeline.cli run --channel CHxx --video NNN --stage script_validation`

### 4.3 台本が破綻している（再生成が必要）
基本:
- Fixerで直すより、reset→再生成のほうが「混入/水増し」を引きずらず安全。
- 超長尺は Marathon で「章単位収束」へ寄せる。

---

## 5) 品質固定（止める仕組み）

品質は「機械チェック → 推論Judge → 最小修正 → それでもダメなら停止」の順で固定する。

### 5.1 決定論チェック（必須）
- 禁則/字数/区切り/括弧上限など（台本本文にURL/脚注/箇条書き等を混ぜない）
- 入口/運用: `ssot/ops/OPS_A_TEXT_GLOBAL_RULES.md`

### 5.2 LLM品質ゲート（推論; 収束上限あり）
- 正本: `ssot/ops/OPS_A_TEXT_LLM_QUALITY_GATE.md`
- 方針:
  - Judge 最大2回、Fix 最大1回、救済（Extend/Expand/Shrink）は必要時だけ最大1回
  - それでもNGなら pending で止め、人間が `assembled_human.md` を直す

### 5.3 意味整合（必要時のみ）
- 正本: `ssot/ops/OPS_SEMANTIC_ALIGNMENT.md`
- 「タイトル語句が本文に出るか」は必須要件ではない（意味の回収を優先）。

---

## 6) 参照切れ/壊れた時の復旧

優先順:
1. `reconcile`（既存出力から status を補正）
2. `script_validation`（原因と fix_hints を出させる）
3. 直せないなら `reset` → 再生成

コマンド:
- `python -m script_pipeline.cli reconcile --channel CHxx --video NNN`
- `python -m script_pipeline.cli run --channel CHxx --video NNN --stage script_validation`
- `python -m script_pipeline.cli reset --channel CHxx --video NNN`

---

## 7) 工程別「何を叩くか」（迷子防止）

実行入口（確定）:
- `ssot/ops/OPS_ENTRYPOINTS_INDEX.md`

工程別の「使う/使わない」確定表:
- `ssot/ops/OPS_SCRIPTS_PHASE_CLASSIFICATION.md`

scripts/ops の棚卸し（参照/誤実行防止）:
- `ssot/ops/OPS_SCRIPTS_INVENTORY.md`


# OPS_SCRIPT_PIPELINE_SSOT — 台本量産ロジックの正本（新規/やり直し/超長尺）

目的:
- 「台本が破綻する/水増しする/タイトルとズレる/同じ話を繰り返す」事故を、**手作業やリトライではなく仕組み**で止める。
- 新規作成とやり直し（Redo）を、低知能エージェントでも迷わず実行できる **確定フロー**にする。
- 2〜3時間級（超長尺）でも破綻しない運用を、**全文LLM禁止**を前提に固定する。

この文書は「台本パイプラインの単一SSOT（1枚）」である。詳細は必要時にリンク先へ降りるが、**迷ったら本書の手順を優先**する。

推奨実行（共通）:
- **必ず** `./scripts/with_ytm_env.sh .venv/bin/python ...` を使う（.envロード + venv依存を固定）。
  - `python -m ...` 直叩きは環境差分（依存不足）で詰まりやすい。
  - 例: `./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli status --channel CH10 --video 004`

関連（詳細/分割SSOT）:
- 確定E2Eフロー（観測ベースの正本）: `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`
- 入口固定（4パターン運用）: `ssot/ops/OPS_SCRIPT_FACTORY_MODES.md`
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

### 0.1 全体像（1枚で把握）

運用者が迷子にならないための “固定の流れ” はこれ。

```
Planning SoT (CSV) ──┐
persona/prompt/pattern ─┤  → status.json（ステージ管理）
                      └───────────────┐
                                      v
topic_research（任意）
  v
script_outline（ここで “ズレ” を早期停止: 安い）
  v
chapter_brief
  v
script_draft → script_enhancement → script_review → quality_check
  v
script_validation（禁則=決定論 + 内容=LLM + 意味整合=LLM）
  v
audio_synthesis（必要時のみ）
```

覚え方（概念）:
- SPEC（設計）→ WRITE（執筆）→ ASSEMBLE（結合/磨き込み）→ PATCH（検査/最小修正）

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
- `./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli init --channel CHxx --video NNN --title \"<title>\"`
- `./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli run-all --channel CHxx --video NNN`

出力（主なもの）:
- `content/outline.md`（アウトライン）
- `content/chapters/chapter_*.md`（章草稿）
- `content/assembled.md`（結合）
- `script_validation`（品質ゲート）

### 2.2 長尺（安定化）: セクション分割→組み上げ（Section Compose）
目的:
- 1撃長文より、セクション単位で「迷子/水増し/禁則」を潰して安定化する。

入口（推奨）:
- `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/a_text_section_compose.py --channel CHxx --video NNN`（dry-run）
- `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/a_text_section_compose.py --channel CHxx --video NNN --apply --run-validation`

ルール:
- セクション本文内に `---` を入れない（区切りは組み上げ工程だけ）
- NGは「そのセクションだけ」再生成（最大N回）

### 2.3 超長尺（2〜3時間級）: Marathon（全文LLM禁止）
結論:
- 超長尺は全文をLLMに渡す品質ゲート（Judge/Fix）が破綻しやすい。
- したがって「章設計→章ごと生成→決定論アセンブル→チャンク判定/差し替え」で収束させる。

入口（推奨）:
- `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/a_text_marathon_compose.py --channel CHxx --video NNN --duration-minutes 120 --plan-only`
- `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/a_text_marathon_compose.py --channel CHxx --video NNN --duration-minutes 120`（dry-run）
- `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/a_text_marathon_compose.py --channel CHxx --video NNN --duration-minutes 120 --apply`

注意:
- 超長尺で `script_validation` を回す場合は全文LLMを無効化して決定論チェックだけ使う:
  - `SCRIPT_VALIDATION_LLM_QUALITY_GATE=0 ./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli run --channel CHxx --video NNN --stage script_validation`

### 2.4 コスト設計（低コストで量産するための固定原則）
結論:
- 高コスト工程（章草稿/本文リライト）に入る前に、**低コストの逸脱検出**で止めるのが最も安い。

必須の順序（推奨）:
1. Planning lint（決定論・無料）で混線を潰す  
   - `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/planning_lint.py --channel CHxx --write-latest`
2. アウトライン段階の意味整合ゲート（安い）で逸脱を止める（`SCRIPT_OUTLINE_SEMANTIC_ALIGNMENT_GATE=1`）
3. 章草稿（高コスト）→ 結合
4. `script_validation`（決定論 + 意味整合 + LLM Judge/Fixer）で最終品質を固定

コストを下げるレバー（安全順）:
- まず再実行（cache hit）: LLMルーターは `workspaces/logs/llm_api_cache/` を使い、同一入力は低コストで再現される。
- Redoは `validate` を優先: 既存本文を直して `script_validation` だけ通すのが最安（再生成は高い）。
- Judgeの収束回数: 既定は `SCRIPT_VALIDATION_LLM_MAX_ROUNDS=3`。コスト優先なら `2` に下げる（ただし不合格率が上がる）。
- 超長尺は Marathon: 全文LLMゲートはスキップし、章単位で収束させる（詳細: `ssot/ops/OPS_LONGFORM_SCRIPT_SCALING.md`）。

---

## 3) 新規作成フロー（確定）

1. Planning SoT を確認（タイトル/進捗/補助メタ）
2. `init`（status.json が無ければ）
3. `run-all`（または `next` で段階実行）
4. `script_validation` を通す（落ちたら Fix か redo）
5. 合格した台本だけ音声へ進む

最短コマンド:
- 標準（主線）:
  - `./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli init --channel CHxx --video NNN --title \"<title>\"`
  - `./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli run-all --channel CHxx --video NNN`
  - `./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli run --channel CHxx --video NNN --stage script_validation`
- バッチ運用（推奨: 入口を固定）:
  - `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/script_runbook.py new --channel CHxx --video NNN`

検証例（新規作成: CH10）:
- `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/script_runbook.py new --channel CH10 --video 004`
  - status.json が無ければ自動で初期化され、Planning SoT（`workspaces/planning/channels/CH10.csv`）のタイトルを使う
  - 最後に `script_validation` が意味整合を検査し、既定では `verdict: major`（明らかなズレ）のみ停止（収束可能なら最小リライトを試みる）

---

## 4) やり直し（Redo / Reset）フロー（確定）

Redo は「何を正本として残すか」を固定しないと、参照が混線して破綻する。

### 4.1 CSV（企画）が変わった
原則:
- reset→再生成（旧台本が残ると混乱源）。

コマンド:
- `./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli reset --channel CHxx --video NNN`
  - 調査も消す: `--wipe-research`

### 4.2 人間が本文（assembled_human）を直した
原則:
- 以降（音声/動画）は必ず再生成。
- まず `script_validation` を再実行して品質を担保してから音声へ進む。

コマンド:
- `./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli run --channel CHxx --video NNN --stage script_validation`

### 4.3 台本が破綻している（再生成が必要）
基本:
- Fixerで直すより、reset→再生成のほうが「混入/水増し」を引きずらず安全。
- 超長尺は Marathon で「章単位収束」へ寄せる。

検証例（既存やり直し: CH07-019 以降）:
- まず “既存台本を直して通す” だけなら:
  - `./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli run --channel CH07 --video 019 --stage script_validation`
  - `./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli run --channel CH07 --video 020 --stage script_validation`（以降同様）
  - バッチ（推奨）: `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/script_runbook.py redo --channel CH07 --from 019 --to 030 --mode validate`
- “企画が混線している/ズレが大きいので作り直す” なら:
  - 単発（低レベル）:
    - `./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli reset --channel CH07 --video 019 --wipe-research`
    - `./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli run-all --channel CH07 --video 019`
  - バッチ（入口固定 / 推奨）:
    - `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/script_runbook.py redo-full --channel CH07 --from 019 --to 030 --wipe-research`

途中から再開（手動介入/中断後）:
- `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/script_runbook.py resume --channel CH07 --video 019`

リライト修正（ユーザー指示必須）:
- `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/script_runbook.py rewrite --channel CH07 --video 019 --instruction \"言い回しをもっと理解しやすい表現に\"`

---

## 5) 品質固定（止める仕組み）

品質は「機械チェック → 推論Judge → 最小修正 → それでもダメなら停止」の順で固定する。

### 5.1 決定論チェック（必須）
- 禁則/字数/区切り/括弧上限など（台本本文にURL/脚注/箇条書き等を混ぜない）
- 入口/運用: `ssot/ops/OPS_A_TEXT_GLOBAL_RULES.md`

### 5.2 LLM品質ゲート（推論; 収束上限あり）
- 正本: `ssot/ops/OPS_A_TEXT_LLM_QUALITY_GATE.md`
- 方針:
  - 既定: Judge 最大3回（fail→Fix→Judge→Fix→Judge）、Fix 最大2回、救済（Extend/Expand/Shrink）は必要時のみ（各Fix後に最大1回）
  - それでもNGなら pending で止め、人間が `assembled_human.md` を直す
  - コストを優先して短く止めたい場合は `SCRIPT_VALIDATION_LLM_MAX_ROUNDS=2` に下げる

### 5.2.1 文字数収束（不足/超過の救済: 決定論フォールバックあり）
- 字数不足:
  - Extend→必要ならExpand（追記のみ）で埋める。
  - 事前救済は原則2パスだが、**2パス後に残り不足が小さい場合（`<=1200`）のみ**追加で1パスを許容して「あと少し足りない」事故を潰す（コスト暴走防止）。
- 字数超過:
  - Shrink（削除/圧縮）を実行する。
  - LLMが削り不足を返すケースがあるため、最終的に **決定論トリム（`---` 区切り単位の予算配分）**で必ずレンジ内へ収束させる。
    - 証跡: `status.json: stages.script_validation.details.auto_length_fix_fallback` に `deterministic_budget_trim` を記録。

### 5.3 意味整合（必須: major を止める）
- 正本: `ssot/ops/OPS_SEMANTIC_ALIGNMENT.md`
- 方針: 文字一致ではなく **意味整合**で「企画の訴求 ↔ 台本コア」のズレを止める。
- 実装（確定）:
  - `script_outline` 後に **事前意味整合ゲート**を実行し、アウトライン段階で逸脱を止める（章草稿=高コストに入る前）。
  - `script_validation` で **意味整合ゲート**を実行し、既定で **`verdict: major` のみ停止**（ok/minor は合格）。
    - major は可能なら最小リライトを自動適用して収束させる（収束しなければ pending で停止）。
    - strict にしたい場合は `SCRIPT_VALIDATION_SEMANTIC_ALIGNMENT_REQUIRE_OK=1`（`verdict: ok` 固定で minor/major は停止）。
- 修正（最小リライト）:
  - `./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli semantic-align --channel CHxx --video NNN --apply`
  - minorも直す: `./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli semantic-align --channel CHxx --video NNN --apply --also-fix-minor`
- 注:
  - 「タイトル語句が本文に出るか」は必須要件ではない（意味として回収できているかだけを見る）。
  - ただし「Nつ」などの数の約束は、台本側の `一つ目〜Nつ目` を **決定論でサニティチェック**し、LLMの誤判定で止まる事故を防ぐ。

---

## 6) 参照切れ/壊れた時の復旧

優先順:
1. `reconcile`（既存出力から status を補正）
2. `script_validation`（原因と fix_hints を出させる）
3. 直せないなら `reset` → 再生成

コマンド:
- `./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli reconcile --channel CHxx --video NNN`
- `./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli run --channel CHxx --video NNN --stage script_validation`
- `./scripts/with_ytm_env.sh .venv/bin/python -m script_pipeline.cli reset --channel CHxx --video NNN`

---

## 7) 工程別「何を叩くか」（迷子防止）

実行入口（確定）:
- `ssot/ops/OPS_ENTRYPOINTS_INDEX.md`

工程別の「使う/使わない」確定表:
- `ssot/ops/OPS_SCRIPTS_PHASE_CLASSIFICATION.md`

scripts/ops の棚卸し（参照/誤実行防止）:
- `ssot/ops/OPS_SCRIPTS_INVENTORY.md`

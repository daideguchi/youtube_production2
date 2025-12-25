# OPS_SCRIPT_FACTORY_MODES — 台本生成/やり直し/再開/リライト（入口固定）

目的:
- 「タイトル/企画の趣旨からズレた台本」を **仕組みで止める**。
- 大量運用のため、**叩く入口を1つに固定**し、運用パターンを4つに限定する。
- 人間がフローを100%追えるように、SoTと分岐を図で固定する。

関連（詳細）:
- 台本量産ロジック（単一SSOT）: `ssot/ops/OPS_SCRIPT_PIPELINE_SSOT.md`
- 台本運用（入口/手順）: `ssot/ops/OPS_SCRIPT_GUIDE.md`
- 確定フロー（観測ベース正本）: `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`
- 入力契約（L1/L2/L3）: `ssot/ops/OPS_SCRIPT_INPUT_CONTRACT.md`
- 意味整合ゲート（タイトル/サムネ↔台本）: `ssot/ops/OPS_SEMANTIC_ALIGNMENT.md`

---

## 0) 入口（絶対固定）

台本パイプラインの運用入口は **これだけ**。

- `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/script_runbook.py <MODE> ...`

※ `python -m script_pipeline.cli ...` は低レベルCLI（内部/詳細制御用）。日常運用では入口を増やさない。

---

## 1) SoT（正本）— ここだけ見れば迷子にならない

- Planning SoT（企画の正本）: `workspaces/planning/channels/CHxx.csv`
- Script SoT（ステージ状態の正本）: `workspaces/scripts/{CH}/{NNN}/status.json`
- 台本本文（Aテキストの正本）:
  - 優先: `workspaces/scripts/{CH}/{NNN}/content/assembled_human.md`
  - フォールバック: `workspaces/scripts/{CH}/{NNN}/content/assembled.md`（mirror）

原則:
- **正本は1つ**（SoTを増やすとズレが増える）。
- `assembled_human.md` がある場合、`assembled.md` を手編集しない（混線の元）。

---

## 2) 4つの運用パターン（これ以外は増やさない）

### 2.1 分岐図（最初にこれだけ判断）

```
            ┌───────────────────────────┐
            │ 今やりたいのはどれ？       │
            └───────────────┬───────────┘
                            │
          ┌─────────────────┼─────────────────┐
          │                 │                 │
      新規で作る         完全に作り直す      途中から続ける
     (status無し)     (作り直し確定)       (status有り)
          │                 │                 │
        new            redo-full            resume
                                              │
                                              └── 言い回し等の修正だけ？
                                                  （指示が必須）
                                                      │
                                                   rewrite
```

### 2.2 new（新規で1から台本を書く）

用途:
- `status.json` が無い（新規エピソード）。
- 企画CSV（タイトル/サムネ/企画意図）を元に、台本を最初から生成したい。

入口:
- `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/script_runbook.py new --channel CHxx --video NNN`

到達点（デフォルト）:
- `script_validation` まで（台本の合否確定）。音声は作らない。

### 2.3 redo-full（最初から完全にやり直す）

用途:
- 内容がズレている/破綻している/混入しているため「修正」ではなく「作り直し」。
- 企画CSVが変わった（旧台本を引きずると事故る）。

入口:
- `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/script_runbook.py redo-full --channel CHxx --from NNN --to MMM`
  - 調査も消す（高確度で混入源を潰す）: `--wipe-research`

到達点（デフォルト）:
- `script_validation` まで。

### 2.4 resume（途中から再開）

用途:
- 途中で止まった（LLM失敗/手動介入/中断）。
- 既存出力を活かして、未完了ステージだけ進めたい。

入口:
- `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/script_runbook.py resume --channel CHxx --video NNN`

到達点（デフォルト）:
- `script_validation` まで。

### 2.5 rewrite（リライト修正：ユーザー指示が必須）

用途:
- 「言い回しをもっと理解しやすく」など **意図は同じで表現を直す**。
- タイトル/企画の主題は変えない（変えるなら `redo-full`）。

必須入力（どちらか）:
- `--instruction "<指示>"`
- `--instruction-file <path>`

入口:
- `./scripts/with_ytm_env.sh .venv/bin/python scripts/ops/script_runbook.py rewrite --channel CHxx --video NNN --instruction \"...\"`

正本の扱い:
- 出力は `content/assembled_human.md`（正本）に反映する。
- 反映後は `script_validation` を必ず再実行し、OKになった台本だけ下流（音声）へ進む。

---

## 3) ズレを止める仕組み（要点だけ）

ズレ事故の主因:
- Planning CSV 行の混線（タイトル【…】と企画要約【…】が別テーマ）
- L2ヒント（要約/タグ等）の汚染が、本文生成を別テーマへ引っ張る
- 長尺を「全文LLM」で回して、途中で迷子/反復/薄まりが起きる

仕組み（正本）:
- 入力契約（L1/L2/L3）で **混線時はL2を捨ててL1で書く**: `ssot/ops/OPS_SCRIPT_INPUT_CONTRACT.md`
- `script_validation` で「禁則（決定論）」→「内容品質（LLM Judge）」→「意味整合（タイトル/サムネ↔台本）」を通す
- 超長尺は Marathon（全文LLM禁止・章単位収束）: `ssot/ops/OPS_LONGFORM_SCRIPT_SCALING.md`


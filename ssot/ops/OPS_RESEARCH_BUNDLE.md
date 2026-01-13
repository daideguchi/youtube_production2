# OPS_RESEARCH_BUNDLE — リサーチ/ファクトチェック用「型」と投入手順（OpenRouter代替）

目的:
- `topic_research`（ネタ集め）と `script_validation`（ファクトチェック）が参照する **リサーチ中間生成物の“型”** を固定する。
- Web検索の取得経路（Brave/OpenRouter/手動/対話モードAI 等）が変わっても、**最終的に同じ型へ正規化**できるようにする。

参照（SoT）:
- I/O一覧: `ssot/ops/OPS_IO_SCHEMAS.md`
- 実装（Web検索）: `packages/factory_common/web_search.py`
- 実装（Wikipedia）: `packages/factory_common/wikipedia.py`
- 実装（Fact check）: `packages/factory_common/fact_check.py`
- Runner（topic_research 前処理）: `packages/script_pipeline/runner.py`

---

## 1) まず結論: 現行の“型”（パイプラインが読む/吐く）

動画ディレクトリ:
- `workspaces/scripts/{CH}/{NNN}/`

### 1.1 topic_research が参照する中間（入力/足場）
場所（固定）:
- `content/analysis/research/search_results.json`（Web検索結果）
- `content/analysis/research/wikipedia_summary.json`（Wikipedia 抜粋）
- `content/analysis/research/references.json`（出典リスト）
- `content/analysis/research/research_brief.md`（論点/要約; LLM参照用）

#### search_results.json（schema: ytm.web_search_results.v1）
必須キー:
- `schema`: `"ytm.web_search_results.v1"`
- `provider`: string（例: `brave`, `llm_router:...`, `manual`, `disabled`）
- `query`: string
- `retrieved_at`: UTC ISO（`Z`）
- `hits`: list
  - `title`: string
  - `url`: string（http/https）
  - `snippet`: string|null（省略可）
  - `source`: string|null（省略可）
  - `age`: string|null（省略可）

#### wikipedia_summary.json（schema: ytm.wikipedia_summary.v1）
必須キー:
- `schema`: `"ytm.wikipedia_summary.v1"`
- `provider`: string（例: `wikipedia`, `manual`, `disabled`）
- `query`: string
- `lang`: string（例: `ja`）
- `retrieved_at`: UTC ISO（`Z`）
- `page_title`: string|null
- `page_id`: number|null
- `page_url`: string|null（http/https）
- `extract`: string|null（plaintext）

#### references.json（list[dict]）
必須（運用上）:
- list（空配列も許可）
観測キー例:
- `title`: string
- `url`: string（http/https）
- `type`: string（例: `web`, `paper`）
- `source`: string（省略可）
- `year`: number|null（省略可）
- `note`: string（省略可）
- `confidence`: number（省略可）

### 1.2 script_validation が吐く（ファクトチェック結果）
場所（固定）:
- `content/analysis/research/fact_check_report.json`

#### fact_check_report.json（schema: ytm.fact_check_report.v1）
必須キー（現行実装）:
- `schema`: `"ytm.fact_check_report.v1"`
- `logic_version`: string（例: `v2`）※判定/抽出ロジックの版。差分が出たら再計算するためのキー。
- `generated_at`: UTC ISO（`Z`）
- `provider`: string（例: `codex`, `llm_router:...`, `disabled`）
- `policy`: string（`disabled|auto|required`）
- `verdict`: string（`pass|warn|fail|skipped`）
- `channel`: string（`CHxx`）
- `video`: string（`NNN`）
- `input_fingerprint`: string（sha256）
- `claims`: list
  - `id`: string（`c1` 等）
  - `claim`: string
  - `status`: string（`supported|unsupported|uncertain`）
  - `rationale`: string|null（省略可）
  - `citations`: list（省略可）
    - `source_id`: string（`s1` 等）
    - `url`: string
    - `quote`: string（提供抜粋内の“完全一致”のみ許可）

補足:
- Fact check は **この4ファイル（search/wiki/refs + Aテキスト）だけ**を根拠として判定する（URL/引用の捏造禁止）。

---

## 2) OpenRouter枯渇時の代替: “対話モードAI”で作って投入する

狙い:
- Web検索結果やWikipedia抜粋を「どの経路で集めても」最終的に上記の型へ揃える。
- パイプラインは **型だけを読む**（取得経路を意識しない）。

このために、投入用の“束ね型”を追加する:

### 2.1 投入用 Research Bundle（schema: ytm.research_bundle.v1）
これは **投入ツール専用の入力**（パイプラインは直接読まない）。

トップレベル（期待）:
- `schema`: `"ytm.research_bundle.v1"`
- `generated_at`: UTC ISO（`Z`）
- `channel`: `CHxx`
- `video`: `NNN`
- `topic`: string（省略可）
- `search_results`: dict（`ytm.web_search_results.v1` 相当）
- `wikipedia_summary`: dict（`ytm.wikipedia_summary.v1` 相当）
- `references`: list（`references.json` 相当）
- `research_brief_md`: string（`research_brief.md` の本文）

### 2.2 使い方（テンプレ生成 → 内容作成 → 適用）
1) テンプレ生成:
```
python3 scripts/ops/research_bundle.py template --channel CH01 --video 251 > /tmp/research_bundle_CH01_251.json
```
2) 対話モードAIで内容を埋める（URLは捏造しない）
3) 適用（workspacesへ書き込み）:
```
python3 scripts/ops/research_bundle.py apply --bundle /tmp/research_bundle_CH01_251.json
```

適用結果（固定パスに展開される）:
- `.../content/analysis/research/search_results.json`
- `.../content/analysis/research/wikipedia_summary.json`
- `.../content/analysis/research/references.json`
- `.../content/analysis/research/research_brief.md`

---

## 3) 運用ルール（固定）

- URL/引用の捏造禁止（不確かな場合は入れない）。
- `search_results.json` / `references.json` は **http/https のみ**（ファイルパス等は不可）。
- `research_brief.md` にURLを貼るのは可（referencesが空の時のフォールバック抽出もある）が、出典SoTは `references.json` に集約する。
- `fact_check_report.json` は本文を書き換えない（検証と修正方針まで）。

### 3.1 重要: Web検索は「必須」ではない
- `web_search_policy` は **取得の試行方針**（disabled/auto/required）であり、既定では失敗してもパイプラインは止めない。
- ただし strict 運用として、`SCRIPT_BLOCK_ON_MISSING_RESEARCH_SOURCES=1` のときは `topic_research` の前に
  「検証に使えるURLが0件」なら停止する（手動投入へ誘導）。

# RUNBOOK_JOB_SCRIPT_PIPELINE — 台本生成（end-to-end / THINKデフォルト）

## Runbook metadata
- **Runbook ID**: RUNBOOK_JOB_SCRIPT_PIPELINE
- **ステータス**: Active
- **対象**: 台本（Aテキスト）を用意し、`script_validation` まで確定させる（THINK/pending を含む）
- **最終更新日**: 2026-01-19

## 0. 固定ルール（最重要）
- **THINK がデフォルト**（pending を作って止める。対話型AIエージェントが埋めて進める）。
- **本文生成ルート（台本だけ例外）**: 対話型AIエージェントが **Claude CLI（sonnet 4.5 既定）** を主に使って本文（Aテキスト）を仕上げる。Claude がリミット/失敗なら **Gemini 3 Flash Preview → qwen** の順でフォールバックする。API は **明示した場合のみ**使う。
- **設計図（Blueprint）は必須ゲート**: `./ops claude|gemini|qwen script ... --run` は **Blueprint bundle が未完/placeholder なら停止**する（=`topic_research`/`script_outline`/`script_master_plan` が揃ってから Writer を走らせる）。例外は `YTM_EMERGENCY_OVERRIDE=1` を明示した実行のみ。
- **Writer に渡す入力は自動で増強**: FULL prompt（`prompts/antigravity_gemini/CHxx/CHxx_NNN_FULL_PROMPT.md`）に、Blueprint bundle（outline/research/references/search/master_plan）を自動追記して Writer CLI に渡す（本文には URL/脚注/参照番号を出さない）。
- **Opus はオーナー指示時のみ**（明示して使う）。
- **禁止: qwen の model/provider 指定**（`--model` / `--qwen-model`）。この repo の qwen ヘルパーは model override を受け付けない（= `qwen -p` 固定）。
- **禁止: qwen の auth-type 切替**（`--auth-type`）。この repo の qwen は **qwen-oauth 固定**（Claude/Gemini/OpenAI を qwen 経由で使わない）。
- **禁止: API→THINK の自動フォールバック**（APIが失敗したら停止して報告。勝手にルートを変えない）。
- **codex exec は別ルート**。台本（`script_*`）の自動生成/書き換えに使わない（混線防止）。

## 0.1 設計図（Blueprint）= Codex（Webサーチ＋構成） （固定）

入口（固定）:
```bash
./ops script resume -- --channel CHxx --video NNN --until script_master_plan --max-iter 6
```

この工程で Codex（対話型AIエージェント）が確定させる成果物（正本）:

補足（THINK/pending の形）:
- `./ops script resume ...` は THINK（既定）では **pending を作って停止**する。
  - `workspaces/logs/agent_tasks/pending/script_*__*.json`（runbook を読んで対話型AIエージェントが埋める）

| Blueprint stage | pending task（THINKで生成） | 出力SoT（正本） | 用途 |
| --- | --- | --- | --- |
| topic_research（調査/根拠） | `script_topic_research__*` | `content/analysis/research/search_results.json` / `wikipedia_summary.json` / `research_brief.md` / `references.json` | 本文に URL/脚注を入れずに、根拠はここへ集約する |
| script_outline（構成案） | `script_outline__*` | `content/outline.md` | 章見出し/章数を固定し、迷子を防ぐ |
| script_master_plan（設計図） | （stage実行で生成） | `content/analysis/master_plan.json` | 後段（章ブリーフ/本文/検証）の指針（plan_summary_text 等） |

補足（Webサーチの固定ルート）:
- `search_results.json` / `wikipedia_summary.json` は **Brave/OpenRouter の自動取得に依存しない**（既定は provider=disabled）。必要なときは Codex が sources を集めて投入する（SSOT: `ssot/ops/OPS_RESEARCH_BUNDLE.md`）。
  - ツール（固定）: `python3 scripts/ops/research_bundle.py template/apply`（schema整形 + `references.json` 生成までを一撃で揃える）

## 0.2 台本本文（Aテキスト）を作る “6ルート” （固定表）

| 位置付け | ルート | 実行主体 | 入口 | 入力SoT | 出力SoT | 禁止/注意 |
| --- | --- | --- | --- | --- | --- | --- |
| DEFAULT | Claude CLI で作る（既定） | 対話型AIエージェント | `./ops claude script -- --channel CHxx --video NNN --run` | `prompts/antigravity_gemini/CHxx/CHxx_NNN_FULL_PROMPT.md` | `content/assembled_human.md`（正本）+ `assembled.md`（mirror） | 既定=sonnet 4.5。Opus は指示時のみ。Claudeリミット時は Gemini 3 Flash Preview → qwen。**Blueprint必須**（FULL prompt + blueprint bundle を自動追記） |
| FALLBACK-1 | Gemini CLI で作る | 対話型AIエージェント | `./ops gemini script -- --channel CHxx --video NNN --run` | `prompts/antigravity_gemini/CHxx/CHxx_NNN_FULL_PROMPT.md` | `content/assembled_human.md`（正本）+ `assembled.md`（mirror） | **Blueprint必須**（FULL prompt + blueprint bundle を自動追記）。サイレントfallback禁止 |
| FALLBACK-2 | qwen -p で作る（最終フォールバック） | 対話型AIエージェント | `./ops qwen script -- --channel CHxx --video NNN --run` | `prompts/antigravity_gemini/CHxx/CHxx_NNN_FULL_PROMPT.md` | `content/assembled_human.md`（正本）+ `assembled.md`（mirror） | **Blueprint必須**（FULL prompt + blueprint bundle を自動追記）。**qwen-oauth固定**。**`--auth-type`/`--qwen-model/--model/-m` 禁止**（別課金/別プロバイダへ逃げない） |
| EXPLICIT | LLM API が作る | パイプライン | `./ops api script <MODE> -- --channel CHxx --video NNN` | planning + SSOT | `workspaces/scripts/{CH}/{NNN}/content/assembled.md`（/human があれば優先） | **API失敗→停止**（THINKへ自動フォールバック禁止） |
| INPUT-SOT | antigravity（prompt SoT） | 入力SoT（prompt） | `prompts/antigravity_gemini/**` | MASTER+FULL prompt | （上の Claude/Gemini/Qwen/API が読む） | prompt は git-tracked（勝手に改変しない） |
| FORBIDDEN | codex exec（非対話） | 非対話CLI | `./ops codex <cmd> ...` | SSOT/設定 | （台本本文には使わない） | **script_* は対象外/禁止**（混線防止） |

## 1. 目的（DoD）
- Aテキスト（`assembled_human.md`）を正本として用意し、`script_validation` まで完了させる。

## 2. 実行（入口固定）

### 2.1 本文（Aテキスト）を作る（明示ルート）
ルートA（Claude CLI / 既定・フォールバック内蔵）:
```bash
./ops claude script -- --channel CH06 --video 033 --run
```

ルートB（Gemini CLI / フォールバック用に明示）:
- 入力: `prompts/antigravity_gemini/CHxx/CHxx_NNN_FULL_PROMPT.md`
- 入口:
```bash
./ops gemini script -- --channel CH06 --video 033 --run --gemini-model gemini-3-flash-preview
```

ルートC（qwen CLI / 最終フォールバック・明示）:
```bash
./ops qwen script -- --channel CH06 --video 033 --run
```

### 2.2 `script_validation` まで進める（THINK/pending）
```bash
./ops script resume -- --channel CH06 --video 033 --until script_validation --max-iter 6
```

## 3. 止まったとき
### 3.1 台本系の停止（`script_*`）
- `assembled_human.md`（正本）を直して **同じコマンドで再実行**する。
- APIルートを使って失敗した場合は、まず原因（キー/枯渇/設定）を直してから rerun（自動でTHINKへ切替しない）。

### 3.2 pending が出たとき
- `python scripts/agent_runner.py list`
- `python scripts/agent_runner.py bundle <TASK_ID> --include-runbook`
- runbook の指示どおりに results を投入:
  - `python scripts/agent_runner.py complete <TASK_ID> --content-file /path/to/content.txt`
- 元コマンドを rerun

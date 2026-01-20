# START_HERE — 迷わないための入口

- 入口固定（セッション記帳; 途中で落ちても復旧できる）:
  - 開始: `./ops session start --name dd-<area>-01 --role worker --doing "..." --next "..."`
  - 終了: `./ops session end --name dd-<area>-01`（Slack digestは `--slack auto` が既定）
  - 必須: `LLM_AGENT_NAME` を `--name` と一致させる（ロックの自己無視/記帳の正本）
    - 例: `export LLM_AGENT_NAME=dd-<area>-01`（または `LLM_AGENT_NAME=dd-<area>-01 ./ops ...`）
  - 途中で落ちた/中断した場合:
    - 未完セッション一覧: `./ops session list --agent dd-<area>-01 --open-only`
    - 記帳ログ: `workspaces/logs/ops/sessions/<session_id>/{start.json,end.json}`

- 統一入口（正本/P0ランチャー）: `./ops list`
  - 事前点検: `./ops doctor`
  - 実行モード（迷わない）:
    - 既定: **THINK**（= pending を作って止まる。対話型AIエージェント/人間が埋める）
    - 明示: **API**（= 外部LLM APIを使う。必要なときだけ）
    - 明示: **codex exec**（別ルート。自動フォールバックの代替ではない）
  - 引数転送（重要）: `./ops` の一部サブコマンドで `--channel` などのフラグを渡すときは `--` で区切る（例: `./ops audio -- --channel CHxx --video NNN`）
  - 迷わない用の短縮（強制）:
    - `./ops think <cmd> ...`（常に THINK MODE）
    - `./ops api <cmd> ...`（常に API）
    - `./ops codex <cmd> ...`（常に codex exec。明示した時だけ）
  - ヒント表示（stderr）: default ON（無効化したい時は `YTM_OPS_TIPS=0`）
  - `codex exec` を使う（明示した時だけ）: `./ops ... --llm codex ...`
  - 迷子/復帰（最新の把握）:
    - 進捗ビュー（read-only）: `./ops progress --channel CHxx --format summary`
    - “最新の実行” ポインタ: `./ops latest --channel CHxx --video NNN`
    - 実行タイムライン（opsレジャー）: `./ops history --tail 50 --channel CHxx`
    - 書庫（重いアセット退避の目録）: UI `/archive/` / SSOT: `ssot/ops/OPS_GH_RELEASES_ARCHIVE.md`
    - YouTube貼り付け（タイトル/概要欄/タグをstdoutへ出す）: `./ops youtube meta --channel CHxx --video NNN`（概要欄: `--field description_full`）
    - 処理パターン索引（CLIレシピSSOT）: `./ops patterns list`（正本: `ssot/ops/OPS_EXECUTION_PATTERNS.md`）
  - 復帰コマンド固定（SSOT）: `ssot/ops/OPS_FIXED_RECOVERY_COMMANDS.md`
  - Reconcile（issues→復帰コマンドを配線; dry-run既定）: `./ops reconcile --channel CHxx --video NNN`
  - SSOTの最新ロジック確認: `./ops ssot status`
- SSOTがカオスに見えたら（読む優先順位の固定）: `ssot/SSOT_COMPASS.md`

## 0) 処理ルート（SSOT・迷わない表）

絶対ルール:
- **THINK がデフォルト**（= 自分で推論して埋める）。勝手に外部LLM APIへ逃げない。
- **禁止: API→THINK の自動フォールバック**（失敗したら止めて報告。ルートを変えない）。

| 工程 | 入口（正本） | ルート（固定） |
| --- | --- | --- |
| 台本（設計図=Codex） | `./ops script resume -- --channel CHxx --video NNN --until script_master_plan --max-iter 6` | THINK（pending）。Codex（対話型AIエージェント）が **Webサーチ→設計図（topic_research/outline/master_plan）→検証** を担当する（本文は書かない）。正本: `ssot/agent_runbooks/RUNBOOK_JOB_SCRIPT_PIPELINE.md` |
| 台本（本文=Writer; 既定） | `./ops claude script -- --channel CHxx --video NNN --run` | 外部CLI（既定: sonnet 4.5。opusはオーナー指示時のみ。Claude失敗→Gemini 3 Flash Preview→qwen）。**Blueprint必須**（未完/placeholder なら停止）。入力SoT: `prompts/antigravity_gemini/CHxx/CHxx_NNN_FULL_PROMPT.md` + Blueprint bundle（outline/research/master_plan を自動追記） / 出力SoT: `content/assembled_human.md` |
| 台本（明示APIで回す） | `./ops api script <MODE> -- --channel CHxx --video NNN` | API（明示。API失敗→THINK自動フォールバック禁止） |
| 台本（Gemini CLI 明示） | `./ops gemini script -- --channel CHxx --video NNN --run` | 外部CLI（明示。**Gemini 3 Flash 固定**。サイレントfallback禁止） |
| 台本（Qwen CLI 明示） | `./ops qwen script -- --channel CHxx --video NNN --run` | 外部CLI（最終フォールバック。**qwen-oauth固定**。`--auth-type`/`--qwen-model/--model/-m` 禁止） |
| 台本（antigravity prompt SoT） | `prompts/antigravity_gemini/CHxx/CHxx_NNN_FULL_PROMPT.md` | **入力SoT**（Claude CLI / Gemini CLI / qwen / API が読む） |
| codex exec（非対話） | `./ops codex <cmd> ...` | 非対話CLI（read-only）。**台本本文（script_*）には使わない**（混線防止） |
| 台本監査（アノテーション） | `python3 scripts/ops/dialog_ai_script_audit.py scan/mark...` | **LLM API禁止**（対話型AIエージェントが判断して `redo_*` を付与） |
| 音声/TTS（アノテーション確定） | `./ops audio -- --channel CHxx --video NNN` | **推論=対話型AIエージェント / 読みLLM（auditor）禁止**（辞書/override を積み、VOICEVOXは `--prepass` mismatch=0 を合格条件にする）。`SKIP_TTS_READING=1` が既定/必須（lockdown中は `0` 禁止） |
| 動画内画像 | `./ops video ...`（各pattern） | **バッチ**（中断/再実行でresume。例外は“明示して別ルート”） |
| サムネ | `./ops thumbnails build -- --channel CHxx --videos NNN ...` | **Gemini 2.5 Flash Image 固定** + まとめて処理（バッチ運用） |

- 全体像（まず読む）: `ssot/OPS_SYSTEM_OVERVIEW.md`
- 確定フロー: `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`
- 実行入口: `ssot/ops/OPS_ENTRYPOINTS_INDEX.md`
- Gitブランチ運用（main/feature/snapshot）: `ssot/ops/OPS_GIT_BRANCH_POLICY.md`
- 入口〜量産投入直前（参照フレーム）: `ssot/ops/OPS_PREPRODUCTION_FRAME.md`
- 入口〜投入前の入力カタログ（必須/オプション/上書き）: `ssot/ops/OPS_PREPRODUCTION_INPUTS_CATALOG.md`
- 入口〜投入前の修復導線（issue→直す場所）: `ssot/ops/OPS_PREPRODUCTION_REMEDIATION.md`
- Planning運用（CSVの扱い/必須列/投稿済みロック）: `ssot/ops/OPS_PLANNING_CSV_WORKFLOW.md`
- 全体TODO（次に何をやるか）: `ssot/ops/OPS_GLOBAL_TODO.md`
- 台本パイプライン導線: `ssot/ops/OPS_SCRIPT_SOURCE_MAP.md`
- 台本工場（入口固定/5モード）: `ssot/ops/OPS_SCRIPT_FACTORY_MODES.md`
- 台本量産ロジック（正本）: `ssot/ops/OPS_SCRIPT_PIPELINE_SSOT.md`
- 台本カオス復旧（複数エージェント競合の止血）: `ssot/ops/OPS_SCRIPT_INCIDENT_RUNBOOK.md`
- 量産投入前のProduction Pack（QA/差分ログ）: `ssot/ops/OPS_PRODUCTION_PACK.md`
- 企画の上書き/追加/部分更新（差分運用）: `ssot/ops/OPS_PLANNING_PATCHES.md`
- モデル使い分け: `ssot/ops/OPS_LLM_MODEL_CHEATSHEET.md`
- 環境・キー設定: `ssot/ops/OPS_ENV_VARS.md`
- タイトル/サムネ↔台本の意味整合: `ssot/ops/OPS_SEMANTIC_ALIGNMENT.md`
- 低知能エージェント運用: `ssot/ops/OPS_AGENT_PLAYBOOK.md`（repo全体のルールは `AGENTS.md`）
- 索引: `ssot/DOCS_INDEX.md`
- キー管理: `GEMINI_API_KEY` などはリポジトリ直下の `.env`（または環境変数）に一元管理。`.gemini_config` や credentials 配下に複製しない。

※ まずは上から順に（確定フロー→入口→環境）だけ押さえれば迷いません。

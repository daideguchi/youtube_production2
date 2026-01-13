# START_HERE — 迷わないための入口

- 入口固定（セッション記帳; 途中で落ちても復旧できる）:
  - 開始: `./ops session start --name dd-<area>-01 --role worker --doing "..." --next "..."`
  - 終了: `./ops session end --name dd-<area>-01`（Slack digestは `--slack auto` が既定）
  - 途中で落ちた/中断した場合:
    - 未完セッション一覧: `./ops session list --agent dd-<area>-01 --open-only`
    - 記帳ログ: `workspaces/logs/ops/sessions/<session_id>/{start.json,end.json}`

- 統一入口（正本/P0ランチャー）: `./ops list`
  - 事前点検: `./ops doctor`
  - 外部LLM APIコストを使わない（サブスク/手動で埋める）: `./ops ... --llm think ...`
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

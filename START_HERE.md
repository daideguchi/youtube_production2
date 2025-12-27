# START_HERE — 迷わないための入口

- 全体像（まず読む）: `ssot/OPS_SYSTEM_OVERVIEW.md`
- 確定フロー: `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`
- 実行入口: `ssot/ops/OPS_ENTRYPOINTS_INDEX.md`
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

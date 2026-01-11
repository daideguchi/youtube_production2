# AGENTS.md — このリポジトリで作業するAIエージェント向けルール（SSOT）

このリポジトリは **複数AIエージェント並列運用**が前提です。  
「迷わない」「壊さない」「ゴミを増やさない」を最優先にしてください。

## 0) 最初に読む（必須）
- 入口: `START_HERE.md`
- 確定フロー（正本）: `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`
- 確定ロジック（最終ルール）: `ssot/reference/【消さないで！人間用】確定ロジック.md`
- 入口索引（何を叩くか）: `ssot/ops/OPS_ENTRYPOINTS_INDEX.md`
- ログ配置（正本）: `ssot/ops/OPS_LOGGING_MAP.md`
- 生成物の保持/削除（正本）: `ssot/plans/PLAN_OPS_ARTIFACT_LIFECYCLE.md`
- エージェント運用（本書の詳細）: `ssot/ops/OPS_AGENT_PLAYBOOK.md`

## 1) 絶対ルール（事故防止）
- 作業開始前に `python scripts/agent_org.py locks --path <file>` でロック確認し、触る範囲に lock を置く（並列衝突防止）。
- SoT（正本）を書き換える場合は、**必ず先にSSOTを更新**してから実装する（フロー/ロジックの誤解を防ぐ）。
- パス直書き禁止: `Path(__file__).parents[...]` ではなく `factory_common.paths` を使う（移設に耐えるため）。
- 機械的等間隔分割は禁止（契約/品質）: cues/セクション分割は必ず文脈ベース。

## 2) ゴミ削除の基本方針（強制）
- “確実ゴミ”のみ削除可（基準: `ssot/plans/PLAN_LEGACY_AND_TRASH_CLASSIFICATION.md`）。
- tracked な削除は **archive-first**（`backups/graveyard/`）→削除→ `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md` に記録。
- untracked キャッシュ（`__pycache__`, `.pytest_cache`, `.DS_Store`）は随時削除してよい。

## 3) コミット/パッチ（環境差異に備える）
- 原則: 小さく刻んでコミット。
- もし git が使えない/不安定なら、`bash scripts/ops/save_patch.sh` でパッチを保存し、Orchestrator/人間が apply→commit する。
  - 並列運用では **必ずスコープ限定**（`--path ...` または「自分の active lock scopes に自動スコープ」）。全体パッチは `--all` 明示時のみ。
  - 未コミット差分が lock 外に広がっている場合は **直さない/消さない**（board/memo で担当へ連絡）。

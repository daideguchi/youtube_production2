# OPS_SCRIPTS_CLEANUP_CANDIDATES — scripts/ レガシー掃除の候補（要 dd 承認）

目的:
- `ssot/ops/OPS_SCRIPTS_INVENTORY.md` を起点に、**“確実ゴミ” だけ**を減らすための候補リストを管理する。
- 削除は **候補提示 → dd承認 → archive-first → 削除 → `OPS_CLEANUP_EXECUTION_LOG` 記録** の順で行う。

判定ルール（正本）:
- `ssot/plans/PLAN_LEGACY_AND_TRASH_CLASSIFICATION.md` の「確実ゴミ 3条件」を満たすもののみ削除可。
  - 1) `OPS_CONFIRMED_PIPELINE_FLOW` の現行フローで入力/参照されない
  - 2) `rg` 等でコード参照ゼロ（Docs/Legacy言及は除外可）
  - 3) dd が不要と明示確認

---

## 1) 結論（2026-01-09）

- `scripts/**` の中で **「参照ゼロ（Docs含めてもゼロ）」まで確定できるものは現時点で 0 件**。
  - したがって、このファイル作成時点では **削除は未実行**。
- ただし、`OPS_SCRIPTS_INVENTORY` 上で `listed-in-SSOT=no` が 25 件あり、運用導線としてはノイズ源なので、
  **「削除」ではなく「SSOT掲載の漏れ修正（=迷い防止）」** が先に必要（後述）。
- 別途決定（2026-01-09）: 台本3パターン（`台本型`/kata1〜3）運用は廃止。関連スクリプトは archive-first で削除する。

---

## 2) 整理対象（削除ではなく “迷い防止”）

`OPS_SCRIPTS_INVENTORY` の `listed-in-SSOT=no`（=SSOT掲載漏れの疑い）。  
基本方針:
- `apps/packages/ui/scripts` 参照があるものは **削除ではなく SSOT掲載**が推奨。
- `ssot` 参照のみ（コード参照ゼロ）のものは **dd判断（keep/legacy）**。

| script | phase | P | created | updated | refs | example ref | action（提案） |
|---|---:|:--:|---:|---:|---:|---|---|
| `scripts/ops/agent_bootstrap.py` | OPS | P1 | 2025-12-30 | 2025-12-30 | scripts=2 ssot=4 | `scripts/ops/orchestrator_bootstrap.py:70` | SSOT掲載(推奨) |
| `scripts/ops/build_ssot_catalog.py` | OPS | P1 | 2026-01-01 | 2026-01-09 | scripts=6 ssot=4 | `scripts/ops/build_ssot_catalog.py:6` | SSOT掲載(推奨) |
| `scripts/ops/dialog_ai_script_audit.py` | OPS | P1 | 2026-01-01 | 2026-01-01 | ssot=4 | `ssot/ops/OPS_DIALOG_AI_SCRIPT_AUDIT.md:125` | dd判断(keep/legacy) |
| `scripts/ops/episode_progress.py` | OPS | P1 | 2025-12-30 | 2026-01-08 | scripts=6 ssot=3 | `scripts/ops/episode_progress.py:12` | SSOT掲載(推奨) |
| `scripts/ops/fact_check_codex.py` | OPS | P1 | 2025-12-30 | 2025-12-30 | ssot=2 | `ssot/ops/OPS_ENTRYPOINTS_INDEX.md:119` | dd判断(keep/legacy) |
| `scripts/ops/fireworks_keyring.py` | OPS | P1 | 2026-01-02 | 2026-01-06 | packages=1 ssot=9 | `packages/factory_common/ssot_catalog.py:4701` | SSOT掲載(推奨) |
| `scripts/ops/git_write_lock.py` | OPS | P1 | 2025-12-29 | 2026-01-08 | scripts=9 ssot=13 | `scripts/ops/git_write_lock.py:19` | SSOT掲載(推奨) |
| `scripts/ops/lint_llm_config.py` | OPS | P1 | 2025-12-28 | 2026-01-09 | scripts=1 ssot=2 | `scripts/ops/lint_llm_config.py:6` | SSOT掲載(推奨) |
| `scripts/ops/lint_llm_router_config.py` | OPS | P1 | 2026-01-09 | 2026-01-09 | scripts=3 ssot=2 | `scripts/ops/lint_llm_config.py:10` | SSOT掲載(推奨) |
| `scripts/ops/llm_hardcode_audit.py` | OPS | P1 | 2025-12-30 | 2025-12-30 | scripts=1 | `scripts/ops/pre_push_final_check.py:68` | SSOT掲載(推奨) |
| `scripts/ops/ops_cli.py` | OPS | P1 | 2026-01-08 | 2026-01-09 | scripts=1 ssot=2 other=1 | `ops:8` | SSOT掲載(推奨) |
| `scripts/ops/orchestrator_bootstrap.py` | OPS | P1 | 2025-12-30 | 2026-01-08 | ssot=2 | `ssot/ops/OPS_ENTRYPOINTS_INDEX.md:212` | dd判断(keep/legacy) |
| `scripts/ops/pages_script_viewer_index.py` | OPS | P1 | 2025-12-28 | 2026-01-03 | scripts=3 ssot=2 other=2 | `docs/README.md:14` | SSOT掲載(推奨) |
| `scripts/ops/pages_snapshot_export.py` | OPS | P1 | 2025-12-29 | 2026-01-06 | scripts=2 other=3 | `docs/data/snapshot/channels.json:4` | SSOT掲載(推奨) |
| `scripts/ops/parallel_ops_preflight.py` | OPS | P1 | 2025-12-30 | 2025-12-31 | scripts=2 ssot=2 | `scripts/ops/ops_cli.py:748` | SSOT掲載(推奨) |
| `scripts/ops/planning_assign_script_kata.py` | OPS | P1 | 2025-12-31 | 2025-12-31 | scripts=3 ssot=3 | `scripts/ops/planning_assign_script_kata.py:16` | archive-first削除（決定 2026-01-09） |
| `scripts/ops/pre_push_final_check.py` | OPS | P1 | 2025-12-29 | 2026-01-09 | apps=2 scripts=4 ssot=9 | `apps/ui-backend/backend/README.md:19` | SSOT掲載(推奨) |
| `scripts/ops/prune_broken_scripts.py` | OPS | P1 | 2026-01-01 | 2026-01-01 | ssot=2 | `ssot/ops/OPS_PLANNING_CSV_WORKFLOW.md:266` | dd判断(keep/legacy) |
| `scripts/ops/research_bundle.py` | OPS | P1 | 2025-12-31 | 2025-12-31 | ssot=2 | `ssot/ops/OPS_RESEARCH_BUNDLE.md:120` | dd判断(keep/legacy) |
| `scripts/ops/slack_notify.py` | OPS | P1 | 2026-01-08 | 2026-01-08 | scripts=2 ssot=1 | `scripts/agent_runner.py:76` | SSOT掲載(推奨) |
| `scripts/ops/workspace_snapshot.py` | OPS | P1 | 2026-01-09 | 2026-01-09 | scripts=1 ssot=3 | `scripts/ops/ops_cli.py:1065` | SSOT掲載(推奨) |
| `scripts/ops/yt_dlp_thumbnail_analyze.py` | OPS | P1 | 2025-12-29 | 2025-12-29 | apps=1 scripts=4 | `apps/ui-frontend/src/pages/BenchmarksPage.tsx:1325` | SSOT掲載(推奨) |
| `scripts/regenerate_srt_from_log.py` | AUDIO | P1 | 2026-01-08 | 2026-01-08 | scripts=2 | `scripts/regenerate_srt_from_log.py:10` | SSOT掲載(推奨) |
| `scripts/thumbnails/ch26_make_two_variants.py` | MISC | P1 | 2025-12-29 | 2025-12-30 | ssot=4 | `ssot/plans/PLAN_OPS_PERFORMANCE_BOTTLENECKS.md:16` | dd判断(keep/legacy) |
| `scripts/vision/vision_pack.py` | MISC | P1 | 2026-01-08 | 2026-01-08 | scripts=3 ssot=6 | `scripts/ops/ops_cli.py:1009` | SSOT掲載(推奨) |

---

## 3) dd 確認ポイント（返事テンプレ）

以下だけ返してくれれば次を実行します。

- 「SSOT掲載(推奨)」の 20件は **SSOTに掲載してよい**？（はい/いいえ）
- 「dd判断(keep/legacy)」の 5件はそれぞれ **keep / legacy隔離 / archive-first削除** のどれ？
  - `scripts/ops/dialog_ai_script_audit.py`
  - `scripts/ops/fact_check_codex.py`
  - `scripts/ops/orchestrator_bootstrap.py`
  - `scripts/ops/prune_broken_scripts.py`
  - `scripts/thumbnails/ch26_make_two_variants.py`

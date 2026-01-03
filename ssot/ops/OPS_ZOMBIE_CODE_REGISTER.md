# OPS_ZOMBIE_CODE_REGISTER — ゾンビコード候補台帳（未確定）

この台帳は「**SSOT主線に不要そう**だが、現時点では削除判断できない」候補を、**根拠付き**で列挙する。
目的は **迷子/誤実行/ゾンビ増殖を止める** ことであり、ここに載っているだけでは削除しない。

## 絶対ルール（SSOT準拠）
- 削除は `ssot/plans/PLAN_LEGACY_AND_TRASH_CLASSIFICATION.md` の “確実ゴミ” 条件を満たすもののみ。
- tracked 削除は **archive-first**: `backups/graveyard/` →削除→ `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md` 記録。
- `refs=0`（参照ゼロ）でも「人間が手で叩く運用スクリプト」は普通に存在するため、**refs=0 = ゴミ確定ではない**。

---

## A) scripts/**（棚卸し起点）

根拠:
- `ssot/ops/OPS_SCRIPTS_INVENTORY.md`（scripts全量棚卸し）
- `python3 scripts/ops/repo_ref_audit.py --target <path>`（固定パス文字列参照の監査）

### A-1) `listed-in-SSOT=no` かつ `refs=0`（要オーナー確認）

| path | 観測 | 暫定提案 |
| --- | --- | --- |
| `scripts/ops/lint_llm_config.py` | `configs/llm.yml` 系の lint（現行主線は `llm_router.yaml`） | **legacy候補**: 残すなら SSOT 入口へ昇格 / 不要なら archive→delete |
| `scripts/py` | repo用の python wrapper（手動実行用途の可能性） | **keep候補**: 使うなら SSOT 入口へ記載 / 使わないなら隔離 |
| `scripts/thumbnails/gen_buddha_channel_bases.py` | “一回きりの生成” に見える（要中身確認） | **legacy候補**: 不要なら archive→delete |
| `scripts/thumbnails/portraits_wikimedia.py` | Wikimedia 取得系（要中身確認） | **legacy候補**: 不要なら archive→delete |

### A-2) legacy系 ops（cleanup log に参照が残る）

| path | 観測 | 暫定提案 |
| --- | --- | --- |
| `scripts/ops/archive_thumbnails_legacy_channel_dirs.py` | “掃除ツール”として有用だが常用入口ではない | **ops/cleanup に残す**（危険度/手順をSSOT明記） |
| `scripts/ops/prune_video_run_legacy_files.py` | 同上 | **ops/cleanup に残す** |
| `scripts/ops/purge_legacy_agent_task_queues.py` | 同上 | **ops/cleanup に残す** |
| `scripts/ops/import_ch01_legacy_scripts.py` | “一回きりの移行” に見える | **legacy候補**（archive→delete or `_adhoc`隔離） |

---

## B) LLM “旧設定系” の残骸候補（要方針決定）

観測:
- `packages/factory_common/llm_router.py` / `configs/llm_router.yaml` が現行主線。
- 一方で `configs/llm.yml` / `packages/factory_common/llm_client.py` / `packages/factory_common/llm_config.py` が残存し、docs でも言及がある（完全廃止か未確定）。

暫定提案:
- **方針決定が先**:
  - 「LLMは `llm_router` に一本化する」を確定できるなら、`llm.yml` 系 + `llm_client/llm_config` + 関連テストを **legacy隔離→削除** の対象にできる。
  - 併用するなら、SSOT側で「どのフェーズがどちらを使うか」を明記し、運用者が迷わない形に寄せる。

---

## C) “ゾンビではないが誤用リスクが高い” もの（注意喚起）

| path | 観測 | 暫定提案 |
| --- | --- | --- |
| `packages/video_pipeline/src/srt2images/engines/capcut_engine.py` | `run_pipeline --engine capcut` 経由で使えるが、SSOT上は **stub/非主線** | SSOTに「stub/非推奨」を明記し、CLI側で deprecate guard（警告/停止）を検討（削除は後） |

---

## 次に必要な意思決定（ユーザー確認）
1) A-1 の4件は「残す（SSOT入口へ昇格）」か「不要（archive→delete）」か？
2) LLM設定は `llm_router` へ一本化するか？（= `llm.yml` 系の扱い）


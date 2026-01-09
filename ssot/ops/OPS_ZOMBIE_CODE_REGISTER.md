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
| scripts/ops/lint_llm_config.py | legacy名（`configs/llm.yml` 系 lint の名残。CI/Docs drift防止の互換入口） | ✅ 解消（2026-01-09）: 旧実装は archive-first→delete 済み（graveyard: `backups/graveyard/20260108T160909Z__remove_legacy_llm_registry_and_zombie_scripts/`）。現行は compat shim として復活し `scripts/ops/lint_llm_router_config.py` を呼ぶ（正本入口: `lint_llm_router_config.py` / `./ops ssot check`）。 |
| scripts/py | repo用の python wrapper（手動実行用途の可能性） | ✅ 解消（2026-01-08）: archive-first→delete（graveyard: `backups/graveyard/20260108T160909Z__remove_legacy_llm_registry_and_zombie_scripts/`） |
| scripts/thumbnails/gen_buddha_channel_bases.py | “一回きりの生成” に見える（要中身確認） | ✅ 解消（2026-01-08）: archive-first→delete（graveyard: `backups/graveyard/20260108T160909Z__remove_legacy_llm_registry_and_zombie_scripts/`） |
| scripts/thumbnails/portraits_wikimedia.py | Wikimedia 取得系（要中身確認） | ✅ 解消（2026-01-08）: archive-first→delete（graveyard: `backups/graveyard/20260108T160909Z__remove_legacy_llm_registry_and_zombie_scripts/`） |

### A-2) legacy系 ops（cleanup log に参照が残る）

| path | 観測 | 暫定提案 |
| --- | --- | --- |
| `scripts/ops/archive_thumbnails_legacy_channel_dirs.py` | “掃除ツール”として有用だが常用入口ではない | **ops/cleanup に残す**（危険度/手順をSSOT明記） |
| `scripts/ops/prune_video_run_legacy_files.py` | 同上 | **ops/cleanup に残す** |
| `scripts/ops/purge_legacy_agent_task_queues.py` | 同上 | **ops/cleanup に残す** |
| scripts/ops/import_ch01_legacy_scripts.py (deleted) | “一回きりの移行” に見える | ✅ 解消（2026-01-09）: archive-first→delete（graveyard: `backups/graveyard/20260109T000025Z__remove_import_ch01_legacy_scripts/`） |

---

## B) LLM “旧設定系” の残骸候補（方針確定済み / D-010）

方針（SSOT / `ssot/DECISIONS.md:D-010`）:
- LLM routing の正本は **`configs/llm_router.yaml` + `configs/llm_task_overrides.yaml` + codes/slots**。
- `configs/llm.yml` + `factory_common.llm_config` / `factory_common.llm_client` は **互換/テスト用の legacy**。通常運用では使わない。
  - 2026-01-09: 迷子防止のため、**ロックダウン（`YTM_ROUTING_LOCKDOWN=1` / default ON）では legacy 経由の実行を停止**し、必要なら `YTM_ROUTING_LOCKDOWN=0` または `YTM_EMERGENCY_OVERRIDE=1` で明示的に解除する（debug only）。
- 削除は別PRで archive-first 手順に従って実施する（tracked 削除ログ必須）。

### B-1) “設定SSOTが複数ある”こと自体がゾンビ増殖源

このrepoには **LLM設定の“正本候補”が複数**あり、運用/実装/可視化が分岐して迷子になる。

| ファミリ | 代表ファイル | 現状 | リスク |
| --- | --- | --- | --- |
| Router系（現行主線） | `configs/llm_router.yaml`, `configs/llm_task_overrides.yaml`, `packages/factory_common/llm_router.py` | script/audio/video が主に使用 | ✅ 主線。ここへ統一したい |
| YML系（legacy） | `configs/llm.yml`, `configs/llm.local.yml`, `packages/factory_common/llm_client.py`, `packages/factory_common/llm_config.py` | `llm_client` 以外の実利用がほぼ無い（監査/テスト中心） | “どれが正本か”が崩れる（通常運用ではロックダウンで停止） |
| Registry系（legacy/UI補助） | （deleted; was: configs/llm_registry.json, configs/llm_model_registry.yaml） | ✅ 解消（2026-01-08）: archive-first→delete（graveyard: `backups/graveyard/20260108T160909Z__remove_legacy_llm_registry_and_zombie_scripts/`） | Routerと二重管理になりやすい |

対応（提案）:
- 意思決定は `ssot/DECISIONS.md` の **D-010（LLM設定SSOTの一本化）** に集約する。

### B-2) Registry系（`llm_registry.json` / `llm_model_registry.yaml`）が残っている箇所（移行対象）

| 参照元 | 参照しているもの | 役割 | 暫定提案 |
| --- | --- | --- | --- |
| `apps/ui-backend/backend/main.py` | （deleted; was: configs/llm_registry.json） | UI設定の読み書き（phase→モデル定義） | ✅ 解消（2026-01-08）: `ui_settings.json` のみに統一し、旧 registry の読み書きを撤去 |
| `apps/ui-backend/backend/routers/llm_usage.py` | （deleted; was: configs/llm_model_registry.yaml） | model key の検証/一覧 | ✅ 解消（2026-01-08）: `llm_router.yaml` / `llm_model_codes.yaml` 由来の表示に統一（旧 registry は撤去） |
| `apps/remotion/scripts/gen_belt_from_srt.js` | 両方 | belt生成の旧スクリプト | 使うなら router に統一 / 使わないなら隔離→archive-first |
| `packages/video_pipeline/src/srt2images/nanobanana_client.py` | （deleted; was: configs/llm_registry.json） | legacy分岐の残骸 | ✅ 解消（2026-01-08）: 未使用の `LLM_REGISTRY_PATH` 定数を撤去 |

---

## C) “ゾンビではないが誤用リスクが高い” もの（注意喚起）

| path | 観測 | 暫定提案 |
| --- | --- | --- |
| `packages/video_pipeline/src/srt2images/engines/capcut_engine.py` | `run_pipeline --engine capcut` 経由で使えるが、SSOT上は **stub/非主線** | SSOTに「stub/非推奨」を明記し、CLI側で deprecate guard（警告/停止）を検討（削除は後） |

### C-2) “disabled/placeholder” の残骸（要棚卸し）

| path | 観測 | 暫定提案 |
| --- | --- | --- |
| `packages/video_pipeline/src/srt2images/nanobanana_client.py` | `USE_LEGACY_IMAGE_ROUTER = False` / `_run_mcp` は placeholder | **隔離候補**: 主線が ImageClient に寄った前提なら、legacy/mcp分岐を削る（ただし削除は “確実ゴミ” 判定後） |

---

## D) Script Pipeline の “no-op stage”（解消済み）

| path | 観測 | リスク | 暫定提案 |
| --- | --- | --- | --- |
| `packages/script_pipeline/stages.yaml`（旧 `script_enhancement`） | `outputs: []` により no-op 完了扱いになる問題があった | 誤認により品質/コスト判断が崩れる | **解消**: D-011 に従い主線から除外（2026-01-04） |

---

## E) `packages/script_pipeline/channels/**/channel_info.json` の同期メタ（差分ノイズ源）

| path | 観測 | 暫定提案 |
| --- | --- | --- |
| `packages/script_pipeline/channels/**/channel_info.json` | `synced_at`/`view_count`/`subscriber_count` 等が更新され、tracked 差分ノイズになりやすい | `ssot/DECISIONS.md:D-012` で **動的メタは `workspaces/` へ分離** を確定し、sync出力を移す |

---

## 次に必要な意思決定（ユーザー確認）
1) A-1 の4件は「残す（SSOT入口へ昇格）」か「不要（archive→delete）」か？
2) LLM legacy（`llm.yml`/`llm_client`/`llm_config`）をいつ削除するか？（通常運用は既にロックダウンで禁止。削除は archive-first の別PRで実施）

# OPS_SCRIPTS_PHASE_CLASSIFICATION — 工程別「使う/使わない」スクリプト確定表（SSOT）

目的:
- `scripts/` と `scripts/ops/` が散らかっても、**低知能エージェントでも迷わず**同じ入口を叩けるようにする。
- 「絶対に使う（正規入口）」「絶対に使わない（禁止/削除対象）」「一時（adhoc）」を工程別に確定し、誤実行を防ぐ。

正本フロー: `ssot/OPS_CONFIRMED_PIPELINE_FLOW.md`  
入口索引（実行コマンドの一覧）: `ssot/OPS_ENTRYPOINTS_INDEX.md`
全ファイル棚卸し（scripts/ 全量）: `ssot/OPS_SCRIPTS_INVENTORY.md`

---

## 0. 分類（このSSOTで確定）

### P0: 正規入口（絶対に使う）
通常運用で「まず叩く」入口。工程の正本は **P0 だけ**とする。

### P1: 付帯/診断（使うことはあるが主線ではない）
主線の補助・監査・復旧・ヘルスチェック。  
実行はOKだが、P0の代替として使わない（主線を壊しやすい）。

### P2: 禁止（絶対に使わない）
誤誘導・旧設計・品質事故の温床。**実行禁止**。  
原則「archive-first → 削除」を次のcleanupバッチで行う。

### P3: 一時スクリプト（adhoc）
その場限りの検証/一時バッチ。**置き場を固定**して混入を防ぐ（後述）。

---

## 1. 置き場ルール（固定ロジック）

### 1.1 正規入口の置き場
- P0/P1 は `scripts/` または `scripts/ops/` のみ。
- P0/P1 を追加/変更したら、必ず `ssot/OPS_ENTRYPOINTS_INDEX.md` と本書を更新する。

### 1.2 一時スクリプト（P3）の置き場
- 置き場: `scripts/_adhoc/`
- 原則 `.gitignore` で除外（混入防止）。必要があれば「期限付きで」明示的に add する。
- ファイル先頭に必ずメタ情報を書く（テンプレ）:

```
#!/usr/bin/env python3
"""
adhoc: <目的>
owner: <agent/person>
created: YYYY-MM-DD
expires: YYYY-MM-DD
notes: <消し忘れ防止の一言>
"""
```

### 1.3 禁止スクリプト（P2）の扱い
- 参照/依存が0であることを確認してから、`backups/graveyard/` に archive-first → repoから削除。
- 証跡は `ssot/OPS_CLEANUP_EXECUTION_LOG.md` に残す（復元可能性を担保）。

---

## 2. 工程別「絶対に使う」入口（P0）

### Phase A. Planning（企画）
- P0:
  - Planning SoT更新: `workspaces/planning/channels/CHxx.csv`（UI `/progress` でも可）
  - 汚染/欠落検知（決定論）: `python3 scripts/ops/planning_lint.py --csv workspaces/planning/channels/CHxx.csv --write-latest`
  - L3混入クリーナ（決定論・保守）: `python3 scripts/ops/planning_sanitize.py --channel CHxx --write-latest`（dry-run）→ 必要時のみ `--apply`

### Phase B. Script Pipeline（台本生成）
- P0:
  - 生成主線: `python -m script_pipeline.cli next/run-all --channel CHxx --video NNN`
  - 長尺（セクション分割）: `python3 scripts/ops/a_text_section_compose.py --channel CHxx --video NNN --apply --run-validation`
  - 超長尺（Marathon）: `python3 scripts/ops/a_text_marathon_compose.py --channel CHxx --video NNN --duration-minutes 120 --apply`
  - Aテキストlint（決定論）: `python3 scripts/ops/a_text_lint.py --channel CHxx --video NNN --write-latest`
  - 整合スタンプ再付与（決定論）: `python3 scripts/enforce_alignment.py --channels CHxx --apply`
  - 品質/整合の監査（read-only）: `python3 scripts/audit_alignment_semantic.py --channels CHxx --videos NNN`

### Phase C. Audio / TTS（音声・SRT）
- P0:
  - 正規: `python -m script_pipeline.cli audio --channel CHxx --video NNN`
  - 直叩き（必要時）: `PYTHONPATH=\".:packages\" python3 -m audio_tts_v2.scripts.run_tts --channel CHxx --video NNN --input workspaces/scripts/CHxx/NNN/content/assembled.md`

### Phase D. Video（SRT→画像→CapCut）
- P0:
  - 正規: `PYTHONPATH=\".:packages\" python3 -m commentary_02_srt2images_timeline.tools.factory ...`
  - 詳細制御: `PYTHONPATH=\".:packages\" python3 -m commentary_02_srt2images_timeline.tools.auto_capcut_run --channel CHxx --srt <srt> --out workspaces/video/runs/<run_id> ...`

### Phase D'. Remotion（未主線/実験）
- P0（運用上の入口として固定）:
  - バッチ再レンダ: `python3 scripts/ops/render_remotion_batch.py --help`

### Phase E. Thumbnails（独立動線）
- P0:
  - UI（推奨）: `/thumbnails`
  - inventory同期（整合）: `python3 scripts/sync_thumbnail_inventory.py`（通常は start_manager guard で check）

### Phase F. Publish（YouTube）
- P0:
  - 投稿: `python3 scripts/youtube_publisher/publish_from_sheet.py --max-rows 1 --run`

### UI（運用入口）
- P0:
  - 起動: `bash scripts/start_all.sh start`（内部で `apps/ui-backend/tools/start_manager.py start` を呼ぶ）
  - ヘルスチェック: `python3 apps/ui-backend/tools/start_manager.py health --with-guards`

---

## 3. 付帯/診断（P1）

### Coordination / Agent運用
- `python3 scripts/agent_org.py ...`（locks/board/memos）
- `python3 scripts/agent_runner.py ...`（pending/results の運用）
- `bash scripts/think.sh -- <cmd>`（LLM_MODE=think の安全運用）
- `bash scripts/with_agent_mode.sh -- <cmd>`（LLM_MODE=agent の運用）
- `bash scripts/with_ytm_env.sh <cmd>`（`.env` を export してから実行。シェル/Node系に必須）

### Redo（リテイク）運用
- `python3 scripts/list_redo.py --type script|audio|all [--channel CHxx]`
- `python3 scripts/mark_redo_done.py --channel CHxx --videos NNN ... [--type audio|script|all]`
  - UI/redo API が正本だが、CLI が必要な場合はこの入口を使う（lock尊重の改善はTODO）。

### Health / Audit
- `python3 scripts/check_env.py --env-file .env`（start_all内でも実行）
- `python3 scripts/api_health_check.py --base-url http://127.0.0.1:8000`
- `python3 scripts/validate_status_sweep.py --repair-global`（壊れたstatusの補正）
- `python3 scripts/prompt_audit.py`（detect-only）
- OpenRouter疎通:
  - `python3 scripts/openrouter_key_probe.py`
  - `python3 scripts/openrouter_caption_probe.py`

### Cleanup / Restore（運用で使う）
- `python -m scripts.cleanup_workspace --dry-run ...` → OKなら `--run`（統合cleanup）
- `python3 scripts/cleanup_data.py --dry-run` → OKなら `--run`（workspaces/scripts中間物）
- `python3 scripts/ops/cleanup_logs.py --run`（logsローテ）
- `bash scripts/ops/cleanup_caches.sh`（pycache等）
- `python3 scripts/ops/restore_video_runs.py --report ...`（run復旧）
- `bash scripts/run_srt2images.sh ...`（UI内部が呼ぶ wrapper。単体実行は原則デバッグのみ）

---

## 4. 禁止（P2: 絶対に使わない / 削除候補）

※「現行SoTフロー外」かつ「誤誘導/品質事故の温床」になりやすいものを列挙。  
削除は `PLAN_LEGACY_AND_TRASH_CLASSIFICATION` の条件を満たしたものから順に実行する。

- `scripts/validate_b_text.py`（B-text前提の旧QA。現行A-text品質ゲートと衝突）
- `scripts/apply_reading_corrections.py`（巨大な固定辞書・CH02局所。現行TTS辞書運用と衝突しやすい）
- `scripts/openrouter_free_models.py`（運用主線では不要。モデル一覧は `factory_common.llm_router` / configs を正とする）
- `scripts/env_guard.py`（`openrouter_free_models.py` / trend系専用の補助。主線で使用しない）
- `scripts/trend_feed.py` / `scripts/fetch_thumbnail_trends.py` / `scripts/assign_trend_thumbnail.py`（未統合の試作。UI/SSOT動線に載っていない）

---

## 5. 次の整理（実行タスク）

1) `scripts/_adhoc/` を作成し、P3の置き場を固定（gitignoreで除外）  
2) `ssot/OPS_ENTRYPOINTS_INDEX.md` に「P0のみ正規入口」の注記 + start_all/health系の追記  
3) P2 を archive-first → `ssot/OPS_CLEANUP_EXECUTION_LOG.md` 記録 → repo から削除（段階的）

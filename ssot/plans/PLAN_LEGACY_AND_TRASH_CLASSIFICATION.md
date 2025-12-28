# PLAN_LEGACY_AND_TRASH_CLASSIFICATION — レガシー隔離/確実ゴミ判定の超詳細計画

## Plan metadata
- **Plan ID**: PLAN_LEGACY_AND_TRASH_CLASSIFICATION
- **ステータス**: Draft
- **担当/レビュー**: Owner: dd / Reviewer: dd
- **対象範囲 (In Scope)**: repo 全域の「Keep / Legacy隔離 / Trash候補」判定と実行順序
- **非対象 (Out of Scope)**: 実際の削除・物理移動（本PLANは判定と順序のみ）
- **関連 SoT/依存**: `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`, `ssot/plans/PLAN_REPO_DIRECTORY_REFACTOR.md`, `ssot/plans/PLAN_OPS_ARTIFACT_LIFECYCLE.md`
- **最終更新日**: 2025-12-12

---

## 1. 判定ルール（確実ゴミの条件）

「確実ゴミ（Trash）」は以下 **3条件が全て成立**したもののみ。
1. `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md` の現行SoTフローの **どのフェーズでも入力/参照されない**。
2. `rg` 等で **コード参照ゼロ**が確認できる（Docs/Legacyの言及は除外可）。
3. 管理者（dd）が **不要と明示確認**。

> 条件1 or 2が曖昧なものは **Legacy隔離**（read‑only移設）に留め、即削除しない。

### 1.1 実施済み（証跡）
- 2025-12-12: `factory_commentary.egg-info/` と `packages/video_pipeline/{src,ui/src}/memory/` を確実ゴミとして削除（`ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`）。
- 2025-12-12: `packages/video_pipeline/**/runtime/logs/notifications.jsonl` を参照ゼロのコミット残骸として削除（`ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`）。
- 2025-12-13: `packages/video_pipeline/` 直下の残骸（`PROJ.json`, `channel_preset.json`, `persona.txt`, `image_cues.json`）を削除（`ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`。バックアップ作成済み）。
- 2025-12-13: 旧PoC/旧静的物（`legacy/50_tools`, `legacy/docs_old`）を **アーカイブ後に削除**（`ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`）。
- 2025-12-13: legacyアーカイブ（`packages/audio_tts/legacy_archive`, `packages/video_pipeline/tools/archive`）を **アーカイブ後に削除**（`ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`）。
- 2025-12-17: `packages/video_pipeline/src/ui/`（旧テンプレート管理UI）と `tests/test_integration.py` を **archive-first** で削除（`ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`）。
- 2025-12-22: ルート `tools/` と `workspaces/planning/ch01_reference/` を **archive-first** で削除（`ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md` Step 92）。

---

## 2. トップレベル分類（現行実態）

### 2.1 Keep（現行フローで必須 / 依存あり）
- `packages/script_pipeline/`：台本ステージ runner（Phase B）。SoT は `workspaces/scripts/`。
- `packages/audio_tts/`：Strict TTS（Phase C）。成果物 SoT は `workspaces/audio/`。
- `packages/video_pipeline/`：SRT→画像→CapCut主線（Phase D）。成果物 SoT は `workspaces/video/`。
- `packages/factory_common/`：LLM/画像/paths SSOT の共通層。
- `apps/ui-backend/` + `apps/ui-frontend/`：運用UI（Planning/Redo/CapCut/Thumbnails等の実体）。
- `scripts/`：運用CLI群（Redo/Drive/YT/監査/同期など主線で使用）。
- （削除済み）ルート `tools/`：チャンネル別のアドホック保守スクリプト置き場（誤誘導の温床）。現行は `scripts/ops/` と `scripts/_adhoc/` に集約（証跡: `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md` Step 92）。
- `configs/`：LLM/画像/Drive/YT/モデルレジストリ等の設定正本。
- `credentials/`：OAuth token 等の秘密情報（物理移動なし）。
- `workspaces/planning/`：企画/進捗CSVとpersonaの正本（Phase A）。
- `workspaces/thumbnails/`：サムネ SoT（Phase F、独立動線）。
- `ssot/`：設計/運用の正本。
- `workspaces/research/`：研究/ベンチ資料（UI `/research` が参照）。
- `data/`：固定資産（Visual Bible正本・hazard辞書など。現行コード参照あり）。
- `asset/`：BGM/ロゴ/オーバーレイ等の **静的素材の正本（L0, git管理）**。Remotion（`staticFile("asset/...")`）と role asset attach で参照。
- `apps/remotion/`：実験/未使用ラインだが UI/preview/コード入口があるため保持。
- `workspaces/logs/`：運用ログの集約（L3だが運用上参照されるため保持）。
- （撤去対象）ルート直下の互換symlink: `script_pipeline`, `audio_tts`, `video_pipeline`, `factory_common`, `progress`, `thumbnails`, `00_research`, `logs`, `remotion`, `ui/*`

### 2.2 Legacy隔離（現行依存ゼロ、ただし履歴/参照として残す）
- （削除済み / local）`_old/`：旧仕様/退避物。現行コード参照なし → 誤参照防止のためローカル削除（`ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`）。
- （削除済み）`idea/`：思考メモ/試作。現行参照なし → アーカイブ後に削除（`ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`）。
- `factory_commentary.egg-info/`：開発時生成のメタ情報（再生成可能。確実ゴミとして削除済み）

> Stage3で隔離済み。`50_tools/` / `docs/` は過去に存在したが、現在は **アーカイブ後に削除済み**（正本: `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`）。

### 2.3 Trash候補（削除対象・ただし条件3の確認待ち）
- `.venv/`：環境依存。不要なら削除可（条件3待ち）。
- `remotion/node_modules/`：再生成可能なL3。Remotion未使用なら削除可（条件3待ち）。
- ルート `output/`：テスト一時出力用。空運用なら削除可（条件3待ち）。

---

## 3. サブツリーの詳細判定（高リスク領域）

### 3.1 `packages/video_pipeline/`

**ユーザー指摘の「ゴミ候補」検証（結論）**
- `packages/video_pipeline/{input,output,_capcut_drafts}` は **互換symlink**（正本: `workspaces/video/`）。参照0確認後に撤去。

**Keep（現行依存あり）**
- `src/`, `tools/`（archive除く）, `server/`, `config/`, `templates/`, `docs/`, `scripts/`, `tests/`
- `audio_sync_status.json` は `workspaces/video/_state/audio_sync_status.json` が正本（legacy: packages/video_pipeline/progress/audio_sync_status.json）

**Legacy隔離**
- （削除済み）`tools/archive/`：過去版。参照ゼロのためアーカイブ後に削除済み（正本: `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`）。
- `docs/spec_updates/*`：旧設計書/統合前の履歴。
- `backups/`：過去退避（運用で必要なら legacy へ移すだけ）。

**Trash候補（run外の残骸）**
> `rg`参照ゼロ・SoTフロー外。管理者確認後に削除。
- `packages/video_pipeline/images/`（残骸）
- packages/video_pipeline/src/runtime/logs/notifications.jsonl（コード参照なしのコミット残骸）

### 3.2 `packages/audio_tts/`

**Keep**
- `scripts/`, `tts/`, `data/reading_dict/`, `docs/`, `tests/`
- （撤去済み）`packages/audio_tts/artifacts` の互換symlinkは撤去済み（正本: `workspaces/audio/`）

**Legacy隔離**
- （削除済み）`legacy_archive/`：現行依存ゼロのためアーカイブ後に削除済み（正本: `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`）。

**Trash候補（生成物レベル、PLAN_OPS_ARTIFACT_LIFECYCLE準拠で後日削除）**
- `workspaces/audio/_archive_audio/*`（古い中間run）
- `workspaces/audio/final/*/*/chunks/*`
- `workspaces/scripts/.../audio_prep/*`
※ ただし **published/ready 確認後のみ**（Stage6で自動化）。

### 3.3 `packages/script_pipeline/`

**Keep**
- `cli.py`, `runner.py`, `validator.py`, `stages.yaml`, `templates.yaml`, `tools/`, `channels/`, `audio/`, `config/`
- SoT は `workspaces/scripts/`（撤去済み: `packages/script_pipeline/data` symlink）

**Legacy隔離候補**
- （削除済み）OpenRouter の一時検証メモは archive-first で退避し、repo から削除した（正本: `backups/graveyard/20251225T103329Z__remove_unused_package_docs/manifest.tsv`）。

### 3.4 `apps/ui-*`

**Keep**
- `apps/ui-backend/`, `apps/ui-frontend/`（運用UIの実体）
- `apps/ui-backend/tools/`（assets_sync等）

**Legacy隔離候補**
- UI内の旧 plan/notes は `ssot/completed/` または `backups/graveyard/`（archive-first）へ移動（個別確認）。

### 3.5 `tests/`

**Keep（現行対象）**
- `test_*` のうち `script_pipeline` / `audio_tts` / `video_pipeline` / `factory_common` を直接テストするもの。

**Legacy隔離（旧名依存）**
旧パイプライン名/旧構成に依存するテストは誤参照の原因になりやすいため、**archive-first** で `backups/graveyard/` に退避したうえで repo から削除する（実施済み: `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md` の Step 18）。

対象（削除済み）:
- `tests/test_synthesis_concat.py`
- `tests/test_logger.py`
- `tests/test_preprocess_a_text.py`
- `tests/test_qa.py`
- `tests/test_b_text_builder.py`
- `tests/test_llm_rewriter_openrouter.py`
- `tests/test_llm_adapter.py`
- `tests/test_annotations.py`
- `tests/test_tts_routing.py`
- `tests/test_orchestrator_smoke.py`
- `tests/test_voicepeak_engine.py`
- `tests/test_kana_engine.py`
- `tests/test_llm_rewriter.py`
- `tests/test_pipeline_init_defaults.py`
- `tests/test_b_text_chunker.py`

> 旧パイプライン再現が必要な場合は、graveyard アーカイブから復元して別ブランチ/別リポジトリで扱う（現行 repo に常駐させない）。

---

## 4. 実行順序（安全にゴミを減らすための段階）

1. **Stage1（paths SSOT）前後で“確実ゴミ（条件1-3確定済み）”のみ削除**
   - 例: `__pycache__`, `.pytest_cache`, `.DS_Store`, root `output/*` など（既に実施済み）
2. **Stage3（legacy隔離）で `_old/`, `50_tools/`, `docs/`, `idea/`, `packages/audio_tts/legacy_archive/`, `packages/video_pipeline/tools/archive/` を read‑only 移設**
   - ※ `50_tools/`, `docs/`, `legacy_archive`, `tools/archive` は既にアーカイブ後に削除済み。
3. **Stage6（cleanup自動化）で L2/L3 生成物をステータス連動で整理**
4. **最後に Trash候補（条件3待ち）を管理者確認の上で削除**

---

## 5. 未確定/要確認ポイント（次の調査タスク）

- `data/hazard_readings.yaml` の参照経路が二重化していたが、`packages/audio_tts/tts/risk_utils.py` を修正して **repo root の hazard 辞書を優先**するようにした（2025-12-17）。  
  → 現在は `repo_root()/data/hazard_readings.yaml` → `audio_pkg_root()/data/hazard_readings.yaml` の順で探索する。
- Visual Bible（任意入力）の扱いが揺れやすい（SoT の固定が必要）。  
  → 現行は「run_dir に保存（例: `workspaces/video/runs/{run_id}/visual_bible.json`）」を正とし、外部ファイルを使う場合のみ `SRT2IMAGES_VISUAL_BIBLE_PATH` で指定する（`ssot/ops/OPS_ENV_VARS.md` 参照）。
- `scripts/` と `tools/` の中に旧ライン（route1/route2 等）が混在。  
  → Stage3後に「Active / Legacy / Trash」を **ファイル単位で再棚卸し**する。

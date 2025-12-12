# PLAN_LEGACY_AND_TRASH_CLASSIFICATION — レガシー隔離/確実ゴミ判定の超詳細計画

## Plan metadata
- **Plan ID**: PLAN_LEGACY_AND_TRASH_CLASSIFICATION
- **ステータス**: Draft
- **担当/レビュー**: Owner: dd / Reviewer: dd
- **対象範囲 (In Scope)**: repo 全域の「Keep / Legacy隔離 / Trash候補」判定と実行順序
- **非対象 (Out of Scope)**: 実際の削除・物理移動（本PLANは判定と順序のみ）
- **関連 SoT/依存**: `ssot/OPS_CONFIRMED_PIPELINE_FLOW.md`, `ssot/PLAN_REPO_DIRECTORY_REFACTOR.md`, `ssot/PLAN_OPS_ARTIFACT_LIFECYCLE.md`
- **最終更新日**: 2025-12-12

---

## 1. 判定ルール（確実ゴミの条件）

「確実ゴミ（Trash）」は以下 **3条件が全て成立**したもののみ。
1. `OPS_CONFIRMED_PIPELINE_FLOW.md` の現行SoTフローの **どのフェーズでも入力/参照されない**。
2. `rg` 等で **コード参照ゼロ**が確認できる（Docs/Legacyの言及は除外可）。
3. 管理者（dd）が **不要と明示確認**。

> 条件1 or 2が曖昧なものは **Legacy隔離**（read‑only移設）に留め、即削除しない。

### 1.1 実施済み（証跡）
- 2025-12-12: `factory_commentary.egg-info/` と `commentary_02_srt2images_timeline/{src,ui/src}/memory/` を確実ゴミとして削除（`ssot/OPS_CLEANUP_EXECUTION_LOG.md`）。
- 2025-12-12: `commentary_02_srt2images_timeline/**/runtime/logs/notifications.jsonl` を参照ゼロのコミット残骸として削除（`ssot/OPS_CLEANUP_EXECUTION_LOG.md`）。
- 2025-12-13: `commentary_02_srt2images_timeline/` 直下の残骸（`PROJ.json`, `channel_preset.json`, `persona.txt`, `image_cues.json`）を削除（`ssot/OPS_CLEANUP_EXECUTION_LOG.md`。バックアップ作成済み）。

---

## 2. トップレベル分類（現行実態）

### 2.1 Keep（現行フローで必須 / 依存あり）
- `script_pipeline/`：台本SoTとステージ runner（Phase B）。
- `audio_tts_v2/`：Strict TTSと最終 artifacts（Phase C）。
- `commentary_02_srt2images_timeline/`：SRT→画像→CapCut主線（Phase D）。
- `factory_common/`：LLM/画像/今後 paths SSOT の共通層。
- `ui/`：FastAPI+React の運用UI（Planning/Redo/CapCut/Thumbnails等の実体）。
- `scripts/`：運用CLI群（Redo/Drive/YT/監査/同期など主線で使用）。
- `tools/`：現行チャンネル向け補助ツール（例: CH06監査/サムネプロンプト補助）。用途は混在のため現時点で削除不可。
- `configs/`：LLM/画像/Drive/YT/モデルレジストリ等の設定正本。
- `credentials/`：OAuth token 等の秘密情報（物理移動なし）。
- `progress/`：企画/進捗CSVとpersonaの正本（Phase A）。
- `thumbnails/`：サムネ SoT（Phase F、独立動線）。
- `ssot/`：設計/運用の正本。
- `00_research/`：研究/ベンチ資料（UI `/research` が参照）。
- `data/`：固定資産（Visual Bible正本・hazard辞書など。現行コード参照あり）。
- `asset/`：Remotion実験ラインの静的素材参照あり（`remotion/src/Timeline.tsx`）。
- `remotion/`：実験/未使用ラインだが UI/preview/コード入口があるため保持。
- `logs/`：運用ログの集約（L3だが運用上参照されるため保持）。

### 2.2 Legacy隔離（現行依存ゼロ、ただし履歴/参照として残す）
- `_old/`：旧仕様/退避物。現行コード参照なし。
- `50_tools/`：旧PoC群（Remotion旧版/旧SRTライン等）。現行コード参照なし。
- `idea/`：思考メモ/試作。現行参照なし。
- `docs/`：旧静的ビルド/メモ（`docs/static/*` 等）。現行UIは別導線。
- `factory_commentary.egg-info/`：開発時生成のメタ情報（再生成可能。確実ゴミとして削除済み）

> これらは Stage3 で `legacy/` 配下に「copy→verify→mv→symlink(optional)」で隔離する。

### 2.3 Trash候補（削除対象・ただし条件3の確認待ち）
- `.venv/`：環境依存。不要なら削除可（条件3待ち）。
- `remotion/node_modules/`：再生成可能なL3。Remotion未使用なら削除可（条件3待ち）。
- ルート `output/`：テスト一時出力用。空運用なら削除可（条件3待ち）。

---

## 3. サブツリーの詳細判定（高リスク領域）

### 3.1 `commentary_02_srt2images_timeline/`

**ユーザー指摘の「ゴミ候補」検証（結論）**
- `commentary_02_srt2images_timeline/ui/` は **ゴミではない**:
  - `apps/ui-backend/backend/video_production.py` から `commentary_02_srt2images_timeline.ui.server.jobs` を参照しているため、削除すると UI ジョブ運用が破綻する（互換: `ui/backend` は symlink）。
- `commentary_02_srt2images_timeline/examples/` は **存在しない**（現行ツリーにディレクトリ自体が無い）。

**Keep（現行依存あり）**
- `src/`, `tools/`（archive除く）, `ui/`, `config/`, `templates/`, `input/`, `output/`, `progress/audio_sync_status.json`, `logs/`, `memory/`

**Legacy隔離**
- `tools/archive/`：`capcut_bulk_insert_versions/*` 等の過去版。参照ゼロ。
- `docs/spec_updates/*`：旧設計書/統合前の履歴。
- `backups/`：過去退避（運用で必要なら legacy へ移すだけ）。

**Trash候補（run外の残骸）**
> `rg`参照ゼロ・SoTフロー外。管理者確認後に削除。
- `commentary_02_srt2images_timeline/images/`（root直下の残骸）
- `commentary_02_srt2images_timeline/src/runtime/logs/notifications.jsonl`（コード参照なしのコミット残骸）
- `commentary_02_srt2images_timeline/ui/src/runtime/logs/notifications.jsonl`（コード参照なしのコミット残骸）

### 3.2 `audio_tts_v2/`

**Keep**
- `scripts/`, `tts/`, `artifacts/`, `data/reading_dict/`, `docs/`, `tests/`

**Legacy隔離**
- `legacy_archive/`：旧strict移行前のスクリプト/tts実装。現行依存ゼロ。

**Trash候補（生成物レベル、PLAN_OPS_ARTIFACT_LIFECYCLE準拠で後日削除）**
- `artifacts/audio/*`（古い中間run）
- `artifacts/final/*/chunks/*`
- `script_pipeline/.../audio_prep/*`
※ ただし **published/ready 確認後のみ**（Stage6で自動化）。

### 3.3 `script_pipeline/`

**Keep**
- `cli.py`, `runner.py`, `validator.py`, `stages.yaml`, `templates.yaml`, `tools/`, `data/`（SoT）

**Legacy隔離候補**
- `openrouter_tests_report.md` 等の一時検証メモは docs/legacyへ移動可能（条件3確認待ち）。

### 3.4 `ui/`

**Keep**
- `backend/`, `frontend/`, `tools/`（assets_sync等）

**Legacy隔離候補**
- UI内の旧 plan/notes は `ssot/completed/` または `legacy/docs_old/` へ移動（個別確認）。

### 3.5 `tests/`

**Keep（現行対象）**
- `test_*` のうち `script_pipeline` / `audio_tts_v2` / `commentary_02` / `factory_common` を直接テストするもの。

**Legacy隔離（旧名依存）**
`rg "commentary_01_srtfile_v2" tests` でヒットした以下は Stage3 で `legacy/tests_commentary_01/` に隔離:
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

> これらは「削除」ではなく、旧パイプライン再現/比較用の履歴として保持する。

---

## 4. 実行順序（安全にゴミを減らすための段階）

1. **Stage1（paths SSOT）前後で“確実ゴミ（条件1-3確定済み）”のみ削除**
   - 例: `__pycache__`, `.pytest_cache`, `.DS_Store`, root `output/*` など（既に実施済み）
2. **Stage3（legacy隔離）で `_old/`, `50_tools/`, `docs/`, `idea/`, `audio_tts_v2/legacy_archive/`, `commentary_02/tools/archive/` を read‑only 移設**
3. **Stage6（cleanup自動化）で L2/L3 生成物をステータス連動で整理**
4. **最後に Trash候補（条件3待ち）を管理者確認の上で削除**

---

## 5. 未確定/要確認ポイント（次の調査タスク）

- `data/hazard_readings.yaml` の参照経路が二重化しており、現行 `audio_tts_v2/tts/risk_utils.py` は `audio_tts_v2/data/` を探索するため **root data のhazard辞書が実際には使われていない可能性**。  
  → Stage1後に paths SSOT に寄せて正本位置を1箇所に確定する。
- `commentary_02/data/visual_bible.json` と root `data/visual_bible.json` の二重管理。  
  → 画像パイプライン側の正本を Stage4/LLM統合のタイミングで確定。
- `scripts/` と `tools/` の中に旧ライン（route1/route2 等）が混在。  
  → Stage3後に「Active / Legacy / Trash」を **ファイル単位で再棚卸し**する。

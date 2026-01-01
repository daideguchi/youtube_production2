# OPS_SSOT_SYSTEM_MAP — UIで“全処理”を漏れなく可視化する（SSOT=UI(view)）

目的:
- **人間とAIの認識を100%一致**させるため、実装と運用（SSOT）を **UIで閲覧**できるようにする。
- 「どの入口→どの関数→どのSoT/成果物→どのLLM/プロンプト→どの停止条件」を **一点の漏れなく**辿れる状態を作る。

前提:
- UIは **閲覧専用（read-only）**。SSOT編集は git（PR）で行う。
- 秘密情報（`.env` / `credentials/` / `.git` 等）はUIから見えないようにする。

---

## 1) “全処理”の定義（網羅条件）

このプロダクトにおける「全処理」は、少なくとも次を含む:

1. **Entry points 100%**
   - CLI（`python3 scripts/...` / `python3 -m ...` / `scripts/*.sh`）
   - UI Backend API（FastAPI routes）
   - UI（ページ/主要導線）
2. **Pipeline stages 100%**
   - Script: `packages/script_pipeline/stages.yaml`
   - Video: `auto_run_info.json progress` 等の進捗キー
   - Audio/TTS: `run_tts` の主要ガード/生成物
   - Thumbnails / Publish / Ops scripts
3. **LLM calls 100%**
   - `factory_common.llm_router` / `factory_common.image_client` 経由のタスク呼び出し
4. **SoT/Artifacts 100%（“正本”の場所）**
   - `workspaces/**` と `ssot/**` に限定し、保存先が散らばらないこと

網羅は「人手で埋める」ではなく、**コードからの自動収集 + テストで落とす**で担保する。

---

## 2) SSOT Catalog（機械可読SSOT）

UIが表示する“全体像”の正本として、カタログ（JSON）を定義する。

- **生成元**: repo 内のコード/設定/SSOT
- **出力**: `/api/ssot/catalog`（UIが取得）
- **用途**:
  - フロー図（Phase→Step）
  - Step詳細（入口/実装位置/SoT/LLM/プロンプト/ガード）
  - 逸脱検知（未収録entrypoint/route/taskがあればテスト失敗）

### 2.1 Node ID（会話で迷わないための固定ID）

- IDは安定であること（順序変更で変わらない）
- 表示ラベルは人間向けに番号も併記してよい

推奨:
- `phase` は `A|B|C|D|F|G|O`（Ops）
- `node_id = "<PHASE>/<slug>"`（例: `B/script_validation`）
- 表示ラベル: `B-07 script_validation`（番号は表示用）

---

## 3) UI（read-only）での提供物

### 3.1 SSOT Portal
- `/ssot` : `ssot/` の閲覧（ファイルブラウザ）

### 3.2 System Map（全体像）
- `/ssot/map` : Phase→Step の一覧/検索/グラフ（固定ID付き）

### 3.3 Step Detail（実装まで掘れる）
- `/ssot/process/<node_id>` :
  - 入口（CLI/API/UI）
  - 主要関数のチェーン（file/line）
  - SoT/成果物（パス）
  - LLM（task→model解決→prompt全文/実行ログ）
  - 停止条件（ガード）

### 3.4 Trace（実行結果）
- `/ssot/trace/<trace_key>` :
  - 実際に実行された順序（ステージ/LLM呼び出し/生成物）
  - “期待SSOT（Catalog）”とのズレ

---

## 4) Backend API（閲覧専用）

### 4.1 ファイル閲覧（安全なベース制限）

`/api/research/list` と `/api/research/file` を “read-only file viewer” として使う。

- base（例）:
  - `ssot` / `packages` / `apps` / `repo_scripts` / `prompts` / `configs` / `tests`
  - `workspaces_*`（必要なもののみ）
- 禁止:
  - repo root 直下（`.env` があるため）
  - `credentials/`, `.git/`, `backups/`, `data/`（ポリシーで要検討）

### 4.2 大きいファイルの閲覧

UIで `runner.py` のような大きいファイルも扱うため、`offset/length`（行）での部分読みを提供する。

---

## 5) LLM Trace（プロンプト全文を確実に残す）

目的:
- “どのLLMに、どのプロンプトで、どこから呼んだか” を **実行ログとして100%残す**。

方針:
- `LLM_ROUTING_KEY`（例: `CH01-251` / `run:CH01-251`）があるときに、LLMRouterが JSONL へ追記する。
- 保存先（例）: `workspaces/logs/traces/llm/<LLM_ROUTING_KEY>.jsonl`

注意:
- “prompt全文表示”は運用上必要だが、**秘密鍵/トークンが混入しない設計**を維持する（`.env` をプロンプトに貼らない、ログにも載せない）。

---

## 6) 運用ガード（UIと実装の一致を壊さない）

- `python3 scripts/ops/pre_push_final_check.py` で以下を必須化する:
  - SSOTリンク整合（`ssot_audit`）
  - 直LLM呼び出し禁止（`llm_hardcode_audit`）
  - Python構文チェック（`compileall`）
  - （追加）SSOT catalog が生成でき、網羅条件を満たすこと


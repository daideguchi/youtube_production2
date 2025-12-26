# PLAN_REPO_DIRECTORY_REFACTOR（legacy） — モノレポの大規模ディレクトリ再編計画

> 参照専用（当時のスナップショット）。現行の正本は `ssot/ops/OPS_REPO_DIRECTORY_SSOT.md` を参照。

## Plan metadata
- **Plan ID**: PLAN_REPO_DIRECTORY_REFACTOR
- **ステータス**: In Progress (Stage 3 Complete / Stage 4 Ongoing)
- **担当/レビュー**: Owner: dd / Reviewer: dd
- **対象範囲 (In Scope)**: リポジトリ全体（Python/Node/シェル/SSOT/UI/生成物/旧資産）
- **非対象 (Out of Scope)**: LLMロジック・生成品質・パイプラインのアルゴリズム改変（パス変更に伴う薄い修正は含む）
- **関連 SoT/依存**: `ssot/ops/OPS_REPO_DIRECTORY_SSOT.md`, `packages/factory_common/paths.py`, `workspaces/`, `apps/`, `packages/`, `scripts/start_all.sh`
- **最終更新日**: 2025-12-18

## 0. 現在地（超要約）
すでに大枠の移行（SoTの正本切替・互換symlink・cleanup導線整備）までは完了している。いま必要なのは「散らかり続ける生成物/ログ/旧runの整理を、確定ルールのもとで自動化・習慣化」して、低知能エージェントでも事故らない状態に固定すること。

- ✅ 完了（主なもの）
  - Stage1: Path SSOT 導入（旧パス直書き排除）
  - Stage2: `workspaces/` 正本化（planning/scripts/audio/video/thumbnails/logs/research を切替）
  - Stage3: `legacy/` へ隔離（旧資産/PoC/退避の分離）
  - cleanup/ops の整備（archive-first / lock尊重 / report出力）
- 🔄 進行中（Stage4）
  - 「確実ゴミ」削除の継続（`workspaces/*` の中間物/ログ/旧run）
  - SSOTの継続整理（索引・運用ルール・I/Oの固定）
  - 互換symlinkの撤去計画（参照0確認 → archive-first → 削除）
  - UI統合導線は別PLAN（`PLAN_UI_EPISODE_STUDIO.md`）で段階実装

## 1. 背景と目的
- 生成物/ログ/旧作業物/複数のサブプロジェクトが同一階層に混在し、**「どこが正本でどこが捨てても良い生成物か」**が判別しづらい。
- 旧ロジック（`commentary_01_srtfile_v2` など）やレガシー試作（`_old` など）が現行コードと並列に残り、探索コストと誤参照リスクが高い。
- 目標は **機能を壊さず** に、(1)コード/アプリ/SoT/生成物/レガシーを明確に分離し、(2)パス参照の単一化で将来の整理を容易にすること。

## 2. 成果物と成功条件 (Definition of Done)
- 成果物
  - 新しいトップレベル構成（`apps/`, `packages/`, `workspaces/`, `legacy/` など）への移行完了。
  - **パス解決のSSOT**: `packages/factory_common/paths.py` に全主要パスを集約し、UI/CLI/バッチがこれを参照。
  - 旧パス互換（ルート直下のsymlink）は作らない。入口コマンド/環境変数を正本へ統一して移行コストを吸収する。
  - SSOT/README/運用ドキュメントのパス記述を新構成に同期。
  - `.gitignore` の整理（生成物/環境/ノード依存のコミット防止）。
  - 生成物の保持/削除/アーカイブ規約を `PLAN_OPS_ARTIFACT_LIFECYCLE.md` として SSOT 化し、workspaces 配下の cleanup を自動化。
- 成功条件
  - `scripts/start_all.sh` 経由で UI が起動し、主要ワークスペースが表示・操作できる（Remotion preview は現行未使用の実験ラインなので DoD には含めない）。
  - `PYTHONPATH=".:packages" python3 -m script_pipeline.cli` / `PYTHONPATH=".:packages" python3 -m audio_tts_v2.scripts.run_tts` / `factory-commentary` が新パスで完走。
  - 既存テストのうち現行パイプライン対象（LLM/tts/srt2images/script_pipeline）に関するものが通る。
  - 新規生成物が **決められた workspaces 配下**に集約され、コード階層が肥大化しない。

## 3. スコープ詳細
- **In Scope**
  - ルート直下ディレクトリの分類・再配置。
  - 生成物（logs/output/artifacts/out/node_modules等）とコードの分離。
  - パス参照の抽象化・統一（環境変数 override 含む）。
  - サブプロジェクト（script/audio/image/video/ui）を monorepo の標準構造へ移行。
  - レガシー/研究/退避物の `legacy/` 集約と互換導線整備。
  - SSOT/README/運用記述のパス同期。
- **Out of Scope**
  - LLMプロンプト/推論戦略/品質ロジックの大改造。
  - ステージ定義そのものの変更（`stages.yaml` の意味変更など）。
  - UI機能追加やAPI仕様拡張（パス変更に伴う最小修正は実施）。

## 4. 現状と課題の整理

### 4.1 ルートの実態（調査結果）
**現行の主要カテゴリ**
- **コアパッケージ（Python）**
  - `packages/script_pipeline/`（台本ステージ・SoT= `workspaces/scripts/CHxx/NNN/`）
  - `packages/audio_tts_v2/`（Bテキスト/TTS・final SoT= `workspaces/audio/final/`）
  - `packages/commentary_02_srt2images_timeline/`（SRT→画像/CapCut・run SoT= `workspaces/video/runs/`）
  - `packages/factory_common/`（LLM/画像クライアント等の共通層、paths SSOT）
- **アプリ（UI/動画）**
  - `apps/ui-backend/backend`（FastAPI）
  - `apps/ui-frontend`（React）
  - `apps/remotion/`（Node+Remotion。実験ラインだがコード/preview/UI入口は存在）
- **SoT/運用データ**
  - `workspaces/planning/`（channels CSV/personas/templates/analytics）
  - `workspaces/thumbnails/`（projects.json + assets）
  - `workspaces/research/`（ベンチ/参考台本）
  - `configs/`（LLM/画像/Drive/YT/設定正本）
  - `prompts/`（横断プロンプト: 説明文など）
  - `credentials/`（OAuth token 等）
  - `ssot/`（設計/運用の正本）
- **生成物/キャッシュ**
  - `workspaces/logs/`（jsonl・db 等の集約）
  - `workspaces/video/runs/`, `workspaces/audio/`（巨大生成物は gitignore）
  - `apps/remotion/out/`, `apps/remotion/node_modules/`
  - `.venv/`, `__pycache__/`, `.pytest_cache/` など環境由来
- **互換symlink（撤去対象）**
  - ルート直下の別名: `script_pipeline`, `audio_tts_v2`, `commentary_02_srt2images_timeline`, `factory_common`, `progress`, `logs`, `thumbnails`, `00_research`, `remotion`, `ui/*`
  - packages内の別名: `packages/script_pipeline/data`, `packages/audio_tts_v2/artifacts`, `packages/commentary_02_srt2images_timeline/{input,output,_capcut_drafts}`
- **レガシー/研究/退避**
  - `_old/`（archive/spec/ssot_old など）
  - `workspaces/research/`（ベンチ/参考台本）
  - `idea/`（メモ/下書き）
  - （削除済み）`50_tools/`（旧PoC群）/ `docs/`（旧静的ビルド）はアーカイブ後に削除済み（正本: `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`）
  - （削除済み）ルート `tools/`（チャンネル別のアドホック保守スクリプト。現行は `scripts/_adhoc/` に集約。証跡: `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md` Step 92）

**サイズ（2025-12-12 観測: `du -sh`）**
- `workspaces/video/runs/`: 約22GB（run成果物）
- `workspaces/audio/final/`: 約19GB（final成果物）
- `apps/remotion/`: 約2.2GB（主に `node_modules/`）
- `apps/ui-frontend/` + `apps/ui-backend/`: 約1.6GB
- `_old/`: 約1.7GB（退避物）
- `.venv/`: 約1.4GB（環境）

**トップレベル実態スナップショット（2025-12-12, FS mtime / git last commit）**
| Path | Size (GB) | FS mtime | Git last commit | Notes |
| --- | ---: | --- | --- | --- |
| `progress` | 0.00 | 2025-12-04 20:06:15 | 2025-12-12 |  |
| `script_pipeline` | 0.41 | 2025-12-12 20:55:55 | 2025-12-12 |  |
| `audio_tts_v2` | 18.72 | 2025-12-12 20:55:55 | 2025-12-12 | large artifacts inside |
| `commentary_02_srt2images_timeline` | 22.53 | 2025-12-12 21:56:42 | 2025-12-12 | large artifacts inside |
| `ui` | 1.60 | 2025-12-12 16:30:08 | 2025-12-12 |  |
| `thumbnails` | 0.20 | 2025-12-12 16:30:09 | 2025-12-11 |  |
| `scripts` | 0.00 | 2025-12-12 22:35:17 | 2025-12-12 |  |
| （削除済み）`tools` | - | - | - | 2025-12-22 archive-first で削除（`ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md` Step 92） |
| `configs` | 0.00 | 2025-12-12 22:35:17 | 2025-12-12 |  |
| `factory_common` | 0.00 | 2025-12-12 22:35:17 | 2025-12-12 |  |
| `remotion` | 2.21 | 2025-12-06 09:36:39 | 2025-12-10 |  |
| `logs` | 0.01 | 2025-12-12 21:17:56 | 2025-12-12 | gitignored |
| `data` | 0.00 | 2025-12-12 17:21:17 | 2025-12-11 |  |
| `asset` | 0.04 | 2025-12-11 14:04:35 | 2025-12-11 |  |
| `00_research` | 0.00 | 2025-12-09 21:11:52 | 2025-12-10 |  |
| `docs` | - | - | - | DELETED (archived) |
| `50_tools` | - | - | - | DELETED (archived) |
| `_old` | 1.65 | 2025-12-11 09:41:03 | - | legacy/research |
| `idea` | 0.00 | 2025-12-12 09:04:46 | 2025-12-11 | legacy/research |
| `backups` | 0.00 | 2025-12-12 22:37:22 | - |  |

### 4.2 典型的な混乱ポイント
- **docs/README と実体の乖離**  
  SSOT/README やルート README では `commentary_01_srtfile_v2` が登場するが、実体は `script_pipeline` に移行済み。  
  → 旧名がコード/テスト/ドキュメントに残存し、検索が混乱する。
- **コード階層に生成物が肥大化**
  - `commentary_02_srt2images_timeline/output/` が 200+ run を保持し、探索ノイズ/サイズ増大。
  - `audio_tts_v2/artifacts/` も同様に生成物が直下。
- **パス参照の多重化と直書き**
  - `ui/backend/main.py` が `PROJECT_ROOT / "script_pipeline"` 等を直書き。
  - ルート `scripts/*.py` や `tools/*.py` も `Path("script_pipeline/data/...")` を大量に直書き。
  - 一部は絶対パス（`/Users/dd/...`）が残存。
  → 物理移動のたびに全域修正が必要になる。
- **アドホック scripts の散在**
  - `scripts/` と `tools/` がドメイン別に整理されておらず、保守・再利用が難しい。
  - `scripts/commentary_service.py` 等の旧スクリプトは誤参照の原因になりやすく、archive-first で `backups/graveyard/` に退避したうえで削除済み（`ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md` Step 19）。
- **環境/依存物のコミット**
  - `remotion/node_modules/` がリポジトリ内に存在。
  - `.venv/` 等も混在（必須ではないが、生成物と見分けづらい）。

### 4.3 現行フローと主要生成先
`ssot/reference/REFERENCE_ssot_このプロダクト設計について.md` のフローに沿う現行実装の対応:
1. 企画/進捗 SoT: `workspaces/planning/channels/CHxx.csv`, `workspaces/planning/personas/`
2. 台本 SoT: `workspaces/scripts/CHxx/NNN/`（`content/*.md`, `status.json`）
3. 音声生成:
   - 入力: `workspaces/scripts/.../content/assembled.md`
   - 出力: `workspaces/scripts/.../audio_prep/*.wav/*.srt` + `workspaces/audio/final/...`
4. 画像/動画ドラフト:
   - 入力: `workspaces/video/input/`（SRT/音声同期）
   - 出力: `workspaces/video/runs/<run>/`（image_cues.json, capcut_draft 等）
5. サムネ SoT: `workspaces/thumbnails/projects.json`（画像実体: `workspaces/thumbnails/assets/<CH>/<video>/`）
6. Remotion:
   - 入力: `apps/remotion/input/`
   - 出力: `apps/remotion/out/`
7. 投稿:
   - `scripts/youtube_publisher/` が `workspaces/planning/channels` + Drive から取得して投稿

## 5. 方針・設計概要

### 5.1 最終トップレベル構成（Target root）
最終形は **コード / 実行アプリ / SoT / 生成物 / レガシーを完全分離**した monorepo 標準へ寄せる。

```
repo-root/
├─ apps/                       # 実行アプリ（UI/動画/サーバ）
├─ packages/                   # Python パッケージ群（import 名は維持）
├─ workspaces/                 # SoT + 生成物の唯一の置き場
├─ configs/                    # 設定正本（現状維持）
├─ prompts/                    # LLMプロンプト正本（現状維持）
├─ credentials/                # OAuth/トークン（現状維持）
├─ scripts/                    # ルート運用スクリプト（thin CLI のみ）
├─ ssot/                       # ドキュメント正本（現状維持）
├─ legacy/                     # 旧資産・PoC・退避・履歴（参照専用）
├─ tests/                      # 現行対象テストのみ
└─ pyproject.toml
```

**不変ルール**
- import 名（`script_pipeline`, `audio_tts_v2`, `commentary_02_srt2images_timeline`, `factory_common`）は維持。
- **生成物は必ず `workspaces/` 配下**に集約し、`apps/` と `packages/` に新規生成物を置かない。
- 完了済み計画書は `ssot/completed/` へ移動（SSOT直下は Active/Draft のみ）。

### 5.2 `apps/` 内部構造（実行アプリ）
**目的**: 実行体（サーバ/UI/動画）を “アプリ” として切り出し、依存する Python パッケージは `packages/` から参照する。

```
apps/
├─ ui-backend/
│  ├─ backend/                 # FastAPI 本体（旧 ui/backend）
│  │  ├─ main.py               # 旧 ui/backend/main.py
│  │  ├─ routers/              # 旧 ui/backend/routers/*
│  │  ├─ video_production.py   # 旧 ui/backend/video_production.py
│  │  └─ ...
│  ├─ requirements.txt         # app 固有 deps
│  ├─ README.md
│  └─ run.sh / uvicorn.toml    # 起動導線（scripts/start_all.sh が参照）
├─ ui-frontend/
│  ├─ src/                     # React（旧 ui/frontend/src）
│  ├─ public/
│  ├─ package.json
│  ├─ vite.config.ts / craco
│  └─ README.md
└─ remotion/                   # Remotion (experimental/未使用ライン)
   ├─ src/                     # Remotion Studio（旧 remotion/src）
   ├─ public/
   ├─ scripts/
   ├─ package.json
   ├─ tsconfig.json
   └─ README.md
```

**apps の運用規約**
- `apps/*` から SoT/生成物へアクセスするときは **必ず paths SSOT** を経由。
- Node 依存（`node_modules/`, `out/`）は **apps/remotion のみ**に閉じ、gitignore 対象。

### 5.3 `packages/` 内部構造（Python パッケージ）
**目的**: ドメインロジックをパッケージに閉じ、アプリや運用スクリプトからは import で利用する。

```
packages/
├─ factory_common/
│  ├─ __init__.py
│  ├─ paths.py                 # パス SSOT（新設・最優先）
│  ├─ llm/
│  │  ├─ llm_client.py
│  │  ├─ llm_router.py         # 互換 thin wrapper（最終的に client に統一）
│  │  ├─ llm_config.py
│  │  └─ llm_param_guard.py
│  ├─ images/
│  │  └─ image_client.py
│  └─ utils/
├─ script_pipeline/
│  ├─ __init__.py
│  ├─ cli.py                   # `python -m script_pipeline.cli`
│  ├─ runner.py
│  ├─ validator.py
│  ├─ sot.py
│  ├─ stages.yaml
│  ├─ templates.yaml
│  ├─ prompts/
│  └─ tools/                   # planning_store 等（現行維持）
├─ audio_tts_v2/
│  ├─ __init__.py
│  ├─ tts/                     # orchestrator/adapter/synthesis 等
│  ├─ scripts/                 # run_tts.py 等（CLI）
│  ├─ configs/
│  ├─ data/                    # 辞書など SoT ではない固定資産のみ
│  ├─ docs/
│  └─ tests/
└─ commentary_02_srt2images_timeline/
   ├─ __init__.py
   ├─ src/                     # srt2images/capcut_ui/core 等
   ├─ tools/                   # capcut_bulk_insert 等の CLI
   ├─ scripts/
   ├─ ui/                      # gradio/fastapi stack（必要なら apps へ移す）
   ├─ config/
   ├─ templates/
   ├─ data/                    # visual_bible 等の固定資産のみ
   └─ tests/
```

**packages の運用規約**
- `packages/*` は **コードのみ**。動画/音声/画像 run 成果物は置かない（`workspaces/` へ）。
- `packages/*/data` は “固定資産（辞書/テンプレ/静的JSON）” のみを許可。

### 5.4 `workspaces/` 内部構造（SoT + 生成物）
**目的**: SoT と run 成果物を集約し、物理移動・削除・アーカイブを安全に行えるようにする。  
保持/削除レベルは `PLAN_OPS_ARTIFACT_LIFECYCLE.md` の L0–L3 に準拠。

```
workspaces/
├─ planning/                   # 企画/進捗 SoT（旧 progress）
│  ├─ channels/
│  ├─ personas/
│  ├─ templates/
│  ├─ analytics/
│  └─ _cache/                  # UI用キャッシュ（削除可）
├─ scripts/                    # 台本 SoT（旧 script_pipeline/data）
│  ├─ CHxx/NNN/
│  │  ├─ status.json           # L0
│  │  ├─ content/              # assembled/final/chapters 等
│  │  ├─ audio_prep/           # L2（ready後削除）
│  │  └─ logs/                 # L3
│  ├─ _state/                  # job_queue.jsonl / stage logs
│  └─ _archive/                # L2圧縮保存
├─ audio/                      # 音声成果物（旧 audio_tts_v2/artifacts）
│  ├─ final/CHxx/NNN/           # L0/L1
│  ├─ audio/<engine>/CHxx/NNN/  # L2
│  └─ _archive_audio/           # 古い run
├─ video/                      # 画像/CapCut run（Remotion は experimental/未使用ライン）
│  ├─ runs/<run_id>/            # L0/L1/L2 混在
│  ├─ input/<channel>/          # L2（同期入力）
│  └─ _archive_runs/
├─ thumbnails/                 # サムネ SoT（旧 thumbnails）
│  ├─ projects.json             # L0
│  ├─ assets/CHxx/NNN/           # L0
│  └─ _archive/
├─ research/                   # ベンチ/参考（旧 00_research）
│  └─ ...
└─ logs/                       # 全ログ集約（旧 logs + app logs）
   ├─ pipeline/
   ├─ ui/
   ├─ jobs/
   ├─ llm_usage.jsonl           # L1
   └─ _archive/
```

### 5.5 `legacy/` 内部構造（参照専用）
```
legacy/
├─ _old/                       # 旧退避物
├─ idea/                       # 人間用メモ（参照専用）
└─ commentary_01_srtfile_v2/   # 必要なら stub + README のみ
```

### 5.6 パス解決の単一化（Path SSOT 詳細）
物理移動前に **全パス参照を抽象化**し、移動後の修正範囲を最小化する。

**新設: `packages/factory_common/paths.py`**
- ルート検出:
  - `repo_root()` は `Path(__file__).resolve().parents[...]` ではなく **`pyproject.toml` の探索**で決定。
  - 例外的に環境変数 `YTM_REPO_ROOT` があれば最優先。
- workspace 検出:
  - `workspace_root()` は `YTM_WORKSPACE_ROOT` があればそれを使用。
  - 無ければ `repo_root()/workspaces`。
- ドメイン別 getter（全コードで唯一の入口）
  - planning: `planning_root()`, `channels_csv_path(ch)`, `persona_path(ch)`
  - scripts: `script_data_root()`, `video_root(ch, vid)`, `status_path(ch, vid)`
  - audio: `audio_root()`, `audio_final_dir(ch, vid)`, `audio_intermediate_dir(engine, ch, vid)`
  - video: `video_runs_root()`, `video_run_dir(run_id)`, `video_input_dir(ch)`
  - thumbnails: `thumbnails_root()`, `thumbnail_assets_dir(ch, vid)`
  - logs: `logs_root()`, `pipeline_log_dir(domain)`

**禁止**: `Path("script_pipeline/data")` / `"commentary_02_srt2images_timeline/output"` 等の直書き、絶対パス。

### 5.7 互換戦略（symlink/alias 禁止）
- ルート直下の互換symlink（別名ディレクトリ）は残さない（`ssot/ops/OPS_REPO_DIRECTORY_SSOT.md` 準拠）。
- Python import は `sitecustomize.py` と `PYTHONPATH=".:packages"` で担保する（symlinkに依存しない）。
- 運用の入口は `ssot/ops/OPS_ENTRYPOINTS_INDEX.md` のコマンド例へ統一し、旧パスの“直叩き”を再発させない。
- 旧名 `commentary_01_srtfile_v2` は **コード/Docs から完全消し込み**し、必要なら `legacy/` に stub を置く。

### 5.8 Target Architecture（パッケージ境界と責務の最終形）
**層構造（上→下の依存のみ許可）**
1. **Presentation / Apps 層**: `apps/*`
   - UI/Remotion/サーバ起動。
   - 直接ファイルパスを作らず **paths SSOT + ドメイン API を呼ぶだけ**。
2. **Domain / Packages 層**: `packages/*`
   - 台本、TTS、画像/動画の各ドメインロジック。
   - 互いに import する場合は **共通契約（contracts）経由**。
3. **Common / Factory 層**: `packages/factory_common/*`
   - paths / LLM / Image / 共通ユーティリティ。
4. **Workspace / Data 層**: `workspaces/*`
   - SoT と成果物の物理保存先。コードはここにロジックを置かない。

**ドメイン別 Public API（最終的にこの入口だけ残す）**
- `script_pipeline`
  - CLI: `script_pipeline.cli`（`init/run/next/status/validate/reset`）
  - SoT read/write: `workspaces/scripts/CHxx/NNN/status.json`
  - Content output: `workspaces/scripts/.../content/*`
  - 他ドメインへの橋渡し: **audio/video は “final artifacts only” を書く**
- `audio_tts_v2`
  - CLI: `audio_tts_v2/scripts/run_tts.py`（prepass/resume/strict）
  - Input: `workspaces/scripts/.../content/assembled.md`
  - Output (final): `workspaces/audio/final/CHxx/NNN/*`
  - Intermediate: `workspaces/scripts/.../audio_prep/*`（L2、ready後削除）
- `commentary_02_srt2images_timeline`
  - CLI: `tools/factory.py`, `tools/generate_belt_layers.py`, `tools/capcut_bulk_insert.py`, `tools/safe_image_swap.py`
  - Input: `workspaces/audio/final/.../*.srt|*.wav` を `workspaces/video/input/` へ同期
  - Output (run): `workspaces/video/runs/<run_id>/`
  - Adopted run id は `workspaces/planning/channels/CHxx.csv` に記録
- `apps/ui-backend`
  - API は **workspaces のみを正本として読む/書く**
  - パス解決は `factory_common.paths` のみ
- `apps/remotion`
  - Input: adopted `workspaces/video/runs/<run_id>/remotion/`
  - Output: `workspaces/video/runs/<run_id>/remotion_out/`（L1→published後zip）

**共通契約（contracts）の扱い**
- `status.json`（台本進捗）
- `audio final bundle`（wav/srt/log）
- `image_cues.json` / `belt_config.json` / `capcut_draft_info.json`
これらのスキーマは今後 `packages/factory_common/contracts/*.json` に集約し、
各ドメインは “contracts を満たす最終成果物だけを下流へ渡す”。

### 5.9 Target Flow（データ/ジョブフローの最終形）
**全体ループ**
1. **Planning**  
   - Input: `workspaces/planning/channels/CHxx.csv`
   - Output: 同 CSV の stage 列更新（SoT）
2. **Script**  
   - Input: planning 行 + persona/template  
   - Output: `workspaces/scripts/CHxx/NNN/{status.json,content/*}`
3. **Audio/TTS**  
   - Input: `workspaces/scripts/.../content/assembled.md`
   - Output:  
     - Intermediate: `workspaces/scripts/.../audio_prep/*`  
     - Final: `workspaces/audio/final/CHxx/NNN/*`
4. **Video (CapCut/Images)**  
   - Input sync: `workspaces/audio/final/...` → `workspaces/video/input/...`
   - Output run: `workspaces/video/runs/<run_id>/*`
5. **CapCut Finalize (manual)**  
   - Input: adopted run_dir の CapCut draft
   - Output: ローカル mp4 書き出し → Drive `uploads/final` へアップロード → Publish Sheet の `Drive (final)`/Status を ready に更新
6. **Remotion (optional / experimental)**  
   - Input: adopted run 内 remotion project
   - Output: remotion_out を同 run 配下へ
7. **Publish**  
   - Input: planning 行（Drive final URL / thumbnail / description）
   - Output: planning 行の youtube_id/status 更新
8. **Analytics → Planning**  
   - Input: YouTube API / CSV
   - Output: `workspaces/planning/analytics/CHxx.csv`

**run_id / video_id 規約**
- `video_id = CHxx-NNN`
- `run_id = <channel>_<video>_<yyyymmddHHMM>_<variant>`  
  例: `CH01_220_20251212_1530_v3`

**失敗時の巻き戻し**
- Script 失敗: `status.json` を stage 単位で rollback（既存 `reset` を維持）
- Audio 失敗: `audio_prep/` と `audio/final/` を削除 → prepass から再実行
- Video 失敗: run を `runs/_failed/<run_id>/` に移し、次 run を作る

## 6. 影響範囲と依存関係
- **UI backend**: `apps/ui-backend/backend/main.py`, `apps/ui-backend/backend/video_production.py`, `apps/ui-backend/backend/routers/*`
  - `PROJECT_ROOT/"script_pipeline"`, `"commentary_02_srt2images_timeline"`, `"progress"`, `"thumbnails"` の参照を paths SSOT に置換。
- **UI frontend**: `apps/ui-frontend/src/components/*`
  - `workspaces/research`, `workspaces/thumbnails/assets`, `workspaces/scripts` 表示パスの更新。
- **ルート scripts/tools**: `scripts/*.py`, `tools/*.py`, `scripts/*.sh`
  - `Path("script_pipeline/data")`, `commentary_02_srt2images_timeline/output` 等の直書きを置換。
- **各パッケージ内部**
  - `packages/audio_tts_v2/scripts/run_tts.py` の出力先
  - `packages/commentary_02_srt2images_timeline/tools/*` の input/output/config 参照
  - `packages/script_pipeline/*` の SoT 参照
- **Remotion**
  - `apps/remotion/src/*` の `asset/` 参照、`public/input` symlink、出力 `out/` の移設。
- **Packaging**
  - `pyproject.toml` の `find_packages`/`project.scripts`/`where` 設定更新。
- **Docs**
  - ルート README/SSOT/OPS での旧パス記述整理。
- **テスト**
  - `tests/*` と `audio_tts_v2/tests/*`, `commentary_02_srt2images_timeline/tests/*` の fixture パス更新。
  - 旧 `commentary_01_srtfile_v2` 依存テストは archive-first で `backups/graveyard/` に退避したうえで削除（`ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md` Step 18）。

## 7. マイルストーン / 実装ステップ（超詳細）

### 7.0 進捗（実施済み・安全な前進）

この計画は **壊さない段階移行**（symlink互換を残す）で進める。直近の実施内容は以下。

- 2025-12-13: `packages/`, `workspaces/`, `legacy/` の scaffold + 互換symlink（commit `958add92`）
- 2025-12-13: `legacy/` へ隔離（`50_tools/`, `docs/`, `idea/`）+ 互換symlink（commit `bad4051e`）
- 2025-12-13: 追加の legacy 隔離（`audio_tts_v2/legacy_archive`, `commentary_02_srt2images_timeline/tools/archive`）+ 互換symlink（commit `0a4ed311`）
- 2025-12-13: `packages/factory_common` と `workspaces/research` の互換symlinkを追加（commit `2dfe251f`）
- 2025-12-13: ルート `README.md` を新レイアウト（`packages/`/`workspaces/`/`legacy/`）に追従（commit `0963a21f`）
- 2025-12-13: 旧PoC/旧静的物/参照ゼロのアーカイブをアーカイブ後に削除（`legacy/50_tools`, `legacy/docs_old`, `legacy_archive`, `tools/archive` 等）。記録は `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md` を正とする。

### Stage 0: Preflight / 保護
- [ ] 現行 `main` の git tag を付与（例: `pre-refactor-YYYYMMDD`）。
- [ ] `workspaces_backup/<date>/` を作り、SoT 全域をコピー（planning/scripts/audio/video/thumbnails）。
- [ ] 既存の “唯一の入口 CLI” を再確認し baseline を SSOT に記録:
  - `python -m script_pipeline.cli status --channel CHxx --video NNN`
  - `PYTHONPATH=".:packages" python3 -m audio_tts_v2.scripts.run_tts --channel CHxx --video NNN --prepass`
  - `PYTHONPATH=".:packages" python3 -m commentary_02_srt2images_timeline.tools.factory --help`
  - `scripts/start_all.sh start`（Remotion preview は起動できれば尚良いが失敗してもブロックしない）
- [ ] 以後、移動/削除は必ず **dry-run → archive-first → run** の順で実施。

### Stage 1: Path SSOT 導入（物理移動なし）
1. `factory_common/paths.py` を新設（現行位置。Stage 4 で `packages/` へ移動）
   - [x] `repo_root()`（pyproject探索 + env override）
   - [x] `workspace_root()`（`YTM_WORKSPACE_ROOT`）
   - [x] planning/scripts/audio/video/thumbnails/logs 用 getter を全実装
   - [x] unit test `tests/test_paths.py` を追加（env override/相対→絶対解決）
2. 直書きパスの置換（物理移動はまだしない）
   - [x] `ui/backend/main.py` の `PROJECT_ROOT/"script_pipeline"` 等を paths 経由へ
   - [x] `script_pipeline/*.py` / `script_pipeline/tools/*`
   - [x] `audio_tts_v2/scripts/*.py` / `audio_tts_v2/tts/*`
   - [x] `commentary_02_srt2images_timeline/src|tools|ui/*`
   - [x] ルート `scripts/*.py`, `tools/*.py`, `*.sh`
   - [x] 絶対パス残存チェック: `rg` で旧パス/絶対パスがヒットしない（docstring除く）。
3. stage1 smoke
   - [x] 主要テスト（import smoke + 既存 unit）を実行し green を確認。

### Stage 2: `workspaces/` 抽出（SoT/生成物の段階移設）
> 各サブステップは **copy → verify → mv → symlink → smoke** の 5 フェーズで実施。

2.1 planning
- [x] `workspaces/planning/` 実体化（root symlink `progress` は撤去対象）。
- [x] アーカイブ（復元用）: `backups/graveyard/20251213_133445_progress.tar.gz`
- [x] 実行: `rm workspaces/planning && mv progress workspaces/planning && ln -s workspaces/planning progress`
- [ ] UI の planning/workspace 画面を smoke。

2.2 scripts (台本 SoT)
- [x] `packages/script_pipeline/data` → `workspaces/scripts/` を **mv + symlink cutover**（正本: `scripts/ops/stage2_cutover_workspaces.py`）。
  - メモ: `packages/script_pipeline/data` symlink は Stage4 で撤去。
- [ ] `python -m script_pipeline.cli validate/next` を sample で smoke。

2.3 audio (音声成果物)
- [x] `packages/audio_tts_v2/artifacts` → `workspaces/audio/` を **mv + symlink cutover**（正本: `scripts/ops/stage2_cutover_workspaces.py`）。
- [x] `workspaces/.gitignore` に `audio/**` を追加（巨大生成物を git に出さない）。
- [ ] `run_tts.py` の final sync が新パス（`workspaces/audio/final/...`）を指すことを smoke。

2.4 video (画像/CapCut run)
- [x] `packages/commentary_02_srt2images_timeline/{input,output}` → `workspaces/video/{input,runs}/` を **mv + symlink cutover**（正本: `scripts/ops/stage2_cutover_workspaces.py`）。
- [x] `workspaces/.gitignore` に `video/input/**`, `video/runs/**` を追加（巨大生成物を git に出さない）。
- [ ] `run_id` の採用/非採用が `workspaces/planning/channels` と整合するか spot check。
- [ ] swap/auto_draft/UI の run 一覧が動くか smoke（CapCut主線）。Remotion系の smoke は現行未使用のため optional。

2.5 thumbnails
- [x] `workspaces/thumbnails/` を正本化（root symlink `thumbnails` は撤去対象）。
- [x] `workspaces/thumbnails/_archive/` を作成（archive-first の受け皿）。
- [ ] `projects.json` の `variants[].image_path` が指す物理パスを spot check（必要なら移行スクリプトで正規化）。
- [ ] UI ThumbnailWorkspace を smoke。

2.6 logs
- [x] `logs` → `workspaces/logs/` を **mv + symlink cutover**（正本: `scripts/ops/stage2_cutover_workspaces.py`）。
- [ ] `workspaces/logs/{pipeline,ui,jobs,_archive}` へ段階整理（`ssot/ops/OPS_LOGGING_MAP.md` と整合）。

2.7 research
- [x] `workspaces/research/` 実体化（旧 `00_research` は撤去対象。ルート直下の互換symlinkは禁止）。
- [x] アーカイブ（復元用）: `backups/graveyard/20251213_133243_00_research.tar.gz`
- [x] 実行: `mv 00_research workspaces/research`（旧 alias は作らない）

### Stage 3: `legacy/` への低リスク隔離
- [x] `legacy/` 作成。
- [x] `_old/` → `legacy/_old/`
- [x] `idea/` → `legacy/idea/`
- [x] `50_tools/` → `legacy/50_tools/` → アーカイブ後に削除（hard delete）
- [x] `docs/`（旧静的物）→ `legacy/docs_old/` → アーカイブ後に削除（hard delete）
- [x] 各 README/SSOT の参照を `legacy/...` に修正し “参照専用” を明示。

### Stage 4: `packages/` への Python パッケージ移動
> import 名を変えずに物理位置だけ変える。

- [x] `packages/` 作成。
- [x] `factory_common/` → `packages/factory_common/`
- [x] `script_pipeline/` → `packages/script_pipeline/`（root symlink `script_pipeline` は撤去対象）
- [x] `audio_tts_v2/` → `packages/audio_tts_v2/`
- [x] `commentary_02_srt2images_timeline/` → `packages/commentary_02_srt2images_timeline/`
- [ ] `pyproject.toml` 更新（任意/後回し可。現状は `sitecustomize.py` + `PYTHONPATH=".:packages"` で運用中）:
  - `package_dir={"": "packages"}` を設定
  - `find_packages.where=["packages"]` へ
  - `project.scripts` の entrypoint パス更新
- [ ] `pip install -e .` の import smoke を通す。

### Stage 5: `apps/` へのアプリ移動
- [x] `apps/` 作成。
- [x] `ui/backend` → `apps/ui-backend/backend`（root `ui/backend` は撤去対象）
- [x] `ui/frontend` → `apps/ui-frontend`（root `ui/frontend` は撤去対象）
- [x] `remotion/` → `apps/remotion`（root symlink `remotion` は撤去対象）
- [x] `scripts/start_all.sh` / `apps/ui-backend/tools/start_manager.py` の参照パス更新。
- [x] Remotion preview の入力を `workspaces/video/input` へ統一（`apps/remotion/input` → `workspaces/video/input` symlink。`public/input` は `../input` を参照）。

### Stage 6: Cleanup / deprecation 完了
- [ ] root互換symlink / packages内symlink を撤去（参照0確認 → archive-first → 削除）。
- [ ] `.gitignore` を最終形に揃え、生成物のコミットを防止。
- [ ] SSOT/OPS/README のパス参照を全域更新。
- [ ] `cleanup_workspace` を cron 本番導線へ切替（`PLAN_OPS_ARTIFACT_LIFECYCLE.md` 準拠）。

## 8. 横断チェックリスト（必須ゲート）
- **安全ゲート**
  - [ ] すべての移動/削除は dry-run を先に実施。
  - [ ] L0/SoT を含むディレクトリは archive-first（コピー→整合→移動）。
  - [ ] root互換symlinkは0（`find . -maxdepth 2 -type l` で確認）。
- **整合ゲート**
  - [ ] `configs/`, `prompts/`, `credentials/`, `ssot/` は物理移動しない（パスだけ更新）。
  - [ ] 直書きパスゼロ（`rg` で旧パス/絶対パスがヒットしない）。
  - [ ] planning SoT は `workspaces/planning/channels` のみ（`progress/` を参照しない）。
  - [ ] SoT JSON の最低限スキーマが維持されている（`ssot/ops/OPS_IO_SCHEMAS.md` の必須キーが欠けていない）。
- **動作ゲート**
  - [ ] Stage 1 直後に import smoke + unit tests が green。
  - [ ] Stage 2 の各サブステップ後に該当ドメインの CLI/UI を smoke。
  - [ ] Stage 5 完了後に `scripts/start_all.sh start` が通る（Remotion preview の失敗は non‑blocking）。
- **履歴ゲート**
  - [ ] 変更点は `ssot/history/HISTORY_codex-memory.md` へ日付付きで追記。
  - [ ] 重大な決定（構造/命名/互換期間変更）は本計画の ADR に追記。

## 9. 決定ログ (ADR 簡易版)
- 2025-12-12: 最終構造を `apps/` + `packages/` + `workspaces/` + `legacy/` に統一する方針を採用。
- 2025-12-12: 物理移動より先に **paths SSOT の導入**を行い、置換→検証→移動の順で進める。
- 2025-12-12: 旧パス互換のため一時的に symlink を採用（※2025-12-25 に撤去方針へ更新）。
- 2025-12-25: ルート直下の互換symlinkは禁止し、参照は正本（`apps/`/`packages/`/`workspaces/`）へ統一する。

## 10. リスクと対策
- **リスク: 直書きパスの取り残しでランタイム破綻**
  - 対策: `rg` で全列挙 → PR チェックリスト化 → 置換後に e2e smoke (`scripts/e2e_smoke.sh`)。
- **リスク: SoT の物理移動でデータ欠損/混在**
  - 対策: 移動前に `workspaces_backup/<date>/` へコピー。`migrate_*` スクリプトで idempotent に移行。
- **リスク: UI が旧ディレクトリ名を表示/参照**
  - 対策: backend の paths SSOT に一本化し、frontend は API 由来パスのみ表示する。
- **リスク: node_modules/out など巨大生成物の履歴が必要**
  - 対策: `legacy/` へ移動し、必要なもののみ残す。履歴が不要なものは git 外へ退避。
- **リスク: pyproject/パッケージ移設で import が崩れる**
  - 対策: packages 移設時に `package_dir`/editable install を整備し、import 名は維持。CI/ローカルで import smoke を実施。

## 11. 非対応事項 / バックログ
- 生成品質の再評価/プロンプト統合作業は別計画（例: `PLAN_LLM_PIPELINE_REFACTOR.md`）。
- UIの新機能・画面整理は本計画の後続。
- データモデルの再設計（status.json スキーマ変更など）は本計画外。

## 12. 参照リンク
- `ssot/reference/REFERENCE_ssot_このプロダクト設計について.md`
- `ssot/ops/DATA_LAYOUT.md`
- `ssot/ops/OPS_SCRIPTS_PHASE_CLASSIFICATION.md`（入口/運用）
- `ssot/ops/OPS_AUDIO_TTS_V2.md`（TTS運用）
- `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`（全体フロー）
- `apps/ui-backend/backend/main.py`
- `scripts/start_all.sh`

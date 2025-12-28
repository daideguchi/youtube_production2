# OPS_REPO_DIRECTORY_SSOT — リポジトリのディレクトリ構造（正本）

目的:
- 「どこが正本で、どこが生成物/キャッシュ/退避か」を1枚で確定し、探索コストと誤参照をなくす。
- ゴミ判定・大規模リファクタ・マルチエージェント運用の **判断基準（SSOT）** にする。

関連（より詳細）:
- 現行フロー/SoT: `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`
- データ格納の実態: `ssot/ops/DATA_LAYOUT.md`
- 生成物ライフサイクル: `ssot/plans/PLAN_OPS_ARTIFACT_LIFECYCLE.md`
- レガシー判定/削除基準: `ssot/plans/PLAN_LEGACY_AND_TRASH_CLASSIFICATION.md`
- 入口索引: `ssot/ops/OPS_ENTRYPOINTS_INDEX.md`
- パスSSOT（コード）: `packages/factory_common/paths.py`
- repo rootレイアウト（ガード/許可リスト）: `packages/factory_common/repo_layout.py`

---

## 0. 大原則（壊さないための固定ルール）

- **SoTを分離**: 正本（SoT）/ミラー/生成物/キャッシュ/退避を混在させない。
- **生成物は `workspaces/` に閉じる**: `apps/` と `packages/` に新規生成物を置かない。
- **静的素材は `asset/` をL0（git管理）**: BGM/ロゴ/オーバーレイ等はここが正本。
- **旧資産/試作は repo に常駐させない**: 必要なら archive-first で `backups/graveyard/` に退避し、現行フローに混ぜない。
- **パス直書き禁止**: 新規/移設対応のため、必ず `factory_common.paths` を経由する。

---

## 1. トップレベル構成（Target / 正本）

### 1.1 Code（実装）
- `apps/`:
  - 実行アプリ（UI/サーバ/動画アプリ）。アプリ固有の設定/静的配信はここ。
  - 生成物（ログ/成果物）は `workspaces/` へ。
- `packages/`:
  - Pythonパッケージ群（`script_pipeline`, `audio_tts`, `video_pipeline`, `factory_common` 等）。
  - 注: `script_pipeline` は現行の **パッケージ名**（`packages/script_pipeline/`）。repo root 直下に `script_pipeline/` が見える場合は **旧資産/誤残置**（互換symlink含む）なので撤去対象。
  - 生成物を置かない（例外: テスト用の小さなfixture）。
  - 旧名/互換aliasの履歴は `ssot/history/LEGACY_ALIASES.md` に退避（現行フローでは参照しない）。
- `scripts/`:
  - 運用CLI（thin wrapper / orchestration / ops）。
  - “恒久的なロジック”は原則 `packages/` へ寄せ、`scripts/` は入口に徹する。
- `tests/`:
  - 現行パイプラインのテストのみ（レガシー再現テストは入れない）。

### 1.2 SoT / Artifacts（運用データ・生成物）
- `workspaces/`（SoT + 生成物の唯一の置き場）:
  - planning: `workspaces/planning/`（CSV/Persona等のSoT）
  - scripts: `workspaces/scripts/`（status.json/assembled等のSoT）
  - audio: `workspaces/audio/`（final wav/srt SoT）
  - video: `workspaces/video/`（runs/input/state/archive等）
  - thumbnails: `workspaces/thumbnails/`（projects.json/assets等）
  - logs: `workspaces/logs/`（運用ログの集約先）
  - 注: 音声/動画の巨大生成物（例: `workspaces/audio/final/**`, `workspaces/video/runs/**`）は **gitignore**（SoTはディスク上の正本として扱う）。
- `asset/`（L0/SoT, git管理）:
  - BGM/ロゴ/オーバーレイ/role assets 等の静的素材の正本。
  - cleanup対象外（削除は原則しない）。

### 1.3 Config / Secrets / Docs / Archives
- `configs/`（設定正本）:
  - LLM/画像/Drive/YT等。機密は入れない（鍵は `.env` / `credentials/`）。
- `credentials/`（機密/トークン）:
  - OAuth token / client_secret 等（git管理しない）。
- `docs/`（GitHub Pages 公開ルート）:
  - 現行のGitHub Pages設定が `./docs` を参照しているため、このディレクトリが公開ルート（= 静的サイトの正本）。
  - Script Viewer（台本ビューア）もここに置く（重複防止のため `pages/` は使わない）。
- `prompts/`（索引/ハブ）:
  - **人間/UI向けの“入口”**。実体の正本をここに複製・同期しない（= 二重SoTを作らない）。
  - 参照先の一覧: `prompts/PROMPTS_INDEX.md`
  - 実際に動くプロンプトの正本は原則こちら:
    - `packages/script_pipeline/prompts/**`
    - `packages/script_pipeline/channels/**/script_prompt.txt`
    - `packages/video_pipeline/**`（画像/CapCut系）
- `ssot/`:
  - 設計/運用/計画の正本（このドキュメント含む）。
- `backups/`:
  - archive-first の退避（`backups/graveyard`）やパッチ保存（`backups/patches`）。
  - 実行/参照の入口にしない（復旧時のみ使う）。
  - ローカルの一時作業は `workspaces/_scratch/`（git管理しない）に置く。

---

## 2. 互換（compat）シンボリックリンク方針

このリポジトリでは **ルート直下の互換symlink（別名ディレクトリ）は禁止**。  
理由: 探索ノイズと誤参照の温床になり、並列エージェント運用で事故るため。

- 参照/実装は必ず正本へ:
  - 実装: `apps/`, `packages/`, `scripts/`, `tests/`
  - SoT/生成物: `workspaces/`
- Python import の成立は symlink ではなく `PYTHONPATH=".:packages"`（例: `scripts/with_ytm_env.sh`）で担保する。
- 既存の互換symlinkを削除する場合は、tracked なら **archive-first**（`backups/graveyard/`）→削除→ `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md` に記録する。

---

## 3. 追加・更新の“置き場”ルール（迷わないための表）

| 追加したいもの | 置き場（正） | 例外/注意 |
| --- | --- | --- |
| 永続ロジック（Python） | `packages/<domain>/` | `scripts/` 直下に肥大化させない |
| 実行入口（CLI） | `scripts/` | 中身は薄く、実体は `packages/` へ |
| UI/サーバ | `apps/` | 生成物は `workspaces/` へ |
| 正本データ（SoT） | `workspaces/` | SoTはフェーズごとに1つに固定 |
| 中間生成物/ログ | `workspaces/` | 保持/削除は `PLAN_OPS_ARTIFACT_LIFECYCLE` |
| 静的素材（BGM/ロゴ等） | `asset/` | git管理のL0。cleanup対象外 |
| 設定（非機密） | `configs/` | 機密は `.env` / `credentials/` |
| 設計/運用ドキュメント | `ssot/` | 追加したら `DOCS_INDEX` 更新 |
| 退避/復元用アーカイブ | `backups/` | 実行入口にしない |
| 旧資産/試作（退避） | `backups/graveyard/` | archive-first のみ。実行/同期/SoT にしない |
| ローカル試作（untracked） | `workspaces/_scratch/` | git管理しない。フロー確定前の一時置き場 |

---

## 4. ディレクトリ変更（移設/削除）の標準手順

1) `python scripts/agent_org.py locks --path <target>` でlock確認  
2) 触る範囲にlockを置く（`python scripts/agent_org.py lock ...`）  
3) **先にSSOTを更新**（本書 + 必要なら `OPS_CONFIRMED_PIPELINE_FLOW` / `DATA_LAYOUT` / `ENTRYPOINTS_INDEX`）  
4) 実装（`factory_common.paths` 優先、互換symlinkは作らない）  
5) cleanup系の tracked 削除は **archive-first** → `ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md` 記録  
6) 小さくコミット（1コミット=1目的）  

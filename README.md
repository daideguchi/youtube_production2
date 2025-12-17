# YouTube Master Monorepo

> **重要**: 最新の仕様 / TODO / 進捗は `ssot/` 直下に集約されています。ここは全体の目次として使用し、詳細は `ssot/README.md` と `ssot/DOCS_INDEX.md` を参照してください。

## ディレクトリ概要（現行）
```
factory_commentary/
├─ packages/                       # Target: Pythonパッケージ（当面は互換symlink）
├─ workspaces/                     # Target: SoT+生成物（当面は互換symlink）
├─ legacy/                         # 旧資産/退避（当面は互換symlinkを残す）
├─ progress/                       # 企画/進捗 SoT（互換symlink → workspaces/planning）
├─ script_pipeline/                # 台本 SoT + runner（data/CHxx/NNN）
├─ audio_tts_v2/                   # 音声/TTS（artifacts/final が下流参照の正本）
├─ commentary_02_srt2images_timeline/  # SRT→画像→CapCut（output/<run_id>）
├─ ui/                             # 運用UI（FastAPI + React）
├─ thumbnails/                     # サムネ SoT（projects.json + 旧資産ディレクトリ）
├─ scripts/                        # 運用CLI群（Drive/YT/監査/同期など）
├─ logs/                           # グローバルログ（gitignore）
├─ ssot/                           # Single Source of Truth（設計/運用/計画）
├─ remotion/                       # experimental（現行未運用）
├─ 00_research/                    # research（互換symlink → workspaces/research）
└─ ...
```

## SSOT Quick Links
| カテゴリ | ファイル |
| --- | --- |
| プロジェクト基礎 | `ssot/README.md` |
| Alignment / Checklist | `ssot/OPS_ALIGNMENT_CHECKPOINTS.md` |
| 企画CSV/運用 | `ssot/OPS_PLANNING_CSV_WORKFLOW.md` / `workspaces/planning/channels/CHxx.csv`（互換: `progress/channels/CHxx.csv`） |
| 環境変数 | `ssot/OPS_ENV_VARS.md` |
| Qwen 対話モード | `QWEN.md` / `prompts/README.md` |
| 台本ソースマップ | `ssot/OPS_SCRIPT_SOURCE_MAP.md` |
| スタートガイド | `START_HERE.md` |
| ドキュメント索引 | `ssot/DOCS_INDEX.md` |

## キー管理（Gemini等）
- `GEMINI_API_KEY` などの秘密鍵はリポジトリ直下の `.env` もしくはシェル環境変数に一元管理する。`.gemini_config` や `credentials/*` への複製はしない。
- `.env.example` を参考に必要キーを埋める。既に設定済みの環境変数があればそれが優先される。

## 見る場所・見ない場所（台本ライン）
- 見る: `ssot/**`（正本）、`script_pipeline/data/CHxx/NNN/status.json`（Script SoT）、`workspaces/planning/channels/CHxx.csv`（Planning SoT）
- 見ない: `legacy/**`, `backups/**`（参照専用/退避）

## 参照ルール
- **SSOT以外の文書は参考用**：旧 `docs/` や `commentary_01/.../docs/` は履歴として残すのみで、最新仕様ではありません。
- 進捗や設計方針を更新する際は、必ず `ssot/` 配下に追記し、`ssot/history/HISTORY_codex-memory.md` にログを残してください。
- 個別 README を更新する場合も、SSOT で整合を取ってから行います。

## Drive アップロード（OAuth固定）
- サービスアカウント経由は 2025 仕様で MyDrive に新規作成不可。Drive はユーザー OAuth で運用。
- クライアント: `configs/drive_oauth_client.json`（OAuth クライアント JSON を配置。今は symlink 済み）
- トークン: `credentials/drive_oauth_token.json`（`scripts/drive_oauth_setup.py` で生成・更新）
- .env 必須キー:  
  - `DRIVE_UPLOAD_MODE=oauth`  
  - `DRIVE_OAUTH_CLIENT_PATH=<REPO_ROOT>/configs/drive_oauth_client.json`  
  - `DRIVE_OAUTH_TOKEN_PATH=<REPO_ROOT>/credentials/drive_oauth_token.json`  
  - `DRIVE_FOLDER_ID=1gSkBU59NFC1ioQTvRDK57nZ51hDRLjfJ`（000_YouTube）
- 初回（またはトークン破損時）: `python3 scripts/drive_oauth_setup.py` → ブラウザで許可
- アップロード: `python3 scripts/drive_upload_oauth.py --file <ローカルファイル>`（フォルダ変更時のみ `--folder <id>`）

## YouTube 自動投稿（骨組み）
- ディレクトリ: `scripts/youtube_publisher/`
- OAuth トークン: `credentials/youtube_publisher_token.json`（Drive+Sheets+YouTube スコープ）
- シート: `YT_PUBLISH_SHEET_ID` / `YT_PUBLISH_SHEET_NAME`（.env に追記済み）。ヘッダーは行1に定義済み（A1:X1）。
- 初回: `python3 scripts/youtube_publisher/oauth_setup.py`
- 投稿スクリプト: `python3 scripts/youtube_publisher/publish_from_sheet.py --max-rows 1 --run`  
  （--run を付けないと dry-run。Status=ready かつ YouTube Video ID 空のみ処理。Drive(final) URL から動画取得→YouTubeへアップ→シートに Video ID/Status/UpdatedAt を書き戻し）
- 詳細: `scripts/youtube_publisher/README.md`

---
最終更新: 2025-12-13

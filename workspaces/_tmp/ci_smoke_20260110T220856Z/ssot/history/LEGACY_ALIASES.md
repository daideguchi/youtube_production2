# LEGACY_ALIASES — 廃止した旧名/互換alias（履歴）

目的:
- 過去の名称・互換alias（主に repo root 直下の別名ディレクトリ/シンボリックリンク）を **履歴として隔離**する。
- 現行フロー/SSOT/運用コマンドからは参照しない（迷いどころ・誤参照の温床になるため）。

原則:
- repo root 直下の互換symlink/別名ディレクトリは作らない。
- `apps/`, `packages/`, `scripts/`, `ssot/`, `workspaces/` を正本として参照する。

## 代表的な旧名（例）

- `audio_tts_v2` → `packages/audio_tts/` + `workspaces/audio/`
- `commentary_02_srt2images_timeline` → `packages/video_pipeline/` + `workspaces/video/`
- `script_pipeline`（repo root 直下の別名）→ `packages/script_pipeline/`
- `video_pipeline`（repo root 直下の別名）→ `packages/video_pipeline/`
- `factory_common`（repo root 直下の別名）→ `packages/factory_common/`
- `ui`（repo root 直下の別名）→ `apps/ui-backend/` + `apps/ui-frontend/` + `apps/ui-backend/tools/`
- `thumbnails`（repo root 直下の別名）→ `workspaces/thumbnails/`
- `logs`（repo root 直下の別名）→ `workspaces/logs/`
- `00_research`（repo root 直下の別名）→ `workspaces/research/`


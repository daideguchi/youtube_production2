# packages/

このディレクトリは「Python パッケージ群」を集約する Target 構成（`ssot/PLAN_REPO_DIRECTORY_REFACTOR.md`）のための置き場です。

現時点では段階移行中で、**一部は実体化済み**、残りは互換 symlink のままです。

## 実体化済み（正）
- `packages/factory_common/`（互換: `factory_common` は symlink → `packages/factory_common`）
- `packages/audio_tts_v2/`（互換: `audio_tts_v2` は symlink → `packages/audio_tts_v2`）

## 互換 symlink（未移行）
- `packages/script_pipeline` → `../script_pipeline`
- `packages/commentary_02_srt2images_timeline` → `../commentary_02_srt2images_timeline`

移行が完了したら、ルート直下の互換 symlink 以外の旧実体は廃止し、`packages/` 配下を正本に統一します。

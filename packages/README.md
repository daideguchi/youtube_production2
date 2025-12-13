# packages/

このディレクトリは「Python パッケージ群」を集約する Target 構成（`ssot/PLAN_REPO_DIRECTORY_REFACTOR.md`）のための置き場です。

現時点では **互換のための symlink** を置いています（実体はルート直下に残っています）。

- `packages/factory_common` → `factory_common`
- `packages/script_pipeline` → `script_pipeline`
- `packages/audio_tts_v2` → `audio_tts_v2`
- `packages/commentary_02_srt2images_timeline` → `commentary_02_srt2images_timeline`

移行が完了したら、symlink を廃止して実体を `packages/` 配下へ移します。

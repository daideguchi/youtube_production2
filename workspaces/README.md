# workspaces/

このディレクトリは「SoT（正本データ）+ 生成物（成果物/ログ）」を集約する Target 構成（`ssot/PLAN_REPO_DIRECTORY_REFACTOR.md`）のための置き場です。

現時点では **物理移動を伴わない移行** のため、旧ディレクトリへの symlink を置いています。

- `workspaces/planning` → `progress/`（企画/進捗 CSV）
- `workspaces/scripts` → `script_pipeline/data/`（台本SoT）
- `workspaces/audio` → `audio_tts_v2/artifacts/`（音声成果物）
- `workspaces/video/runs` → `commentary_02_srt2images_timeline/output/`（動画run）
- `workspaces/video/input` → `commentary_02_srt2images_timeline/input/`（動画入力）
- `workspaces/logs` → `logs/`
- `workspaces/research` → `00_research/`
- `workspaces/thumbnails` → `thumbnails/`

パス解決は `factory_common/paths.py` が正本です（`workspaces/` が実体化した時に自動で新パスへ寄ります）。

# workspaces/

このディレクトリは「SoT（正本データ）+ 生成物（成果物/ログ）」を集約する Target 構成（`ssot/plans/PLAN_REPO_DIRECTORY_REFACTOR.md`）のための置き場です。

現時点では多くが **物理移動を伴わない移行** のため、旧ディレクトリへの symlink を置いています。
（例外: `workspaces/research` は実体化済みで、旧 `00_research/` が symlink）

## 互換 symlink（旧パス → 正本）
- `progress/` → `workspaces/planning/`
- `00_research/` → `workspaces/research/`
- `logs/` → `workspaces/logs/`
- `thumbnails/` → `workspaces/thumbnails/`
- `script_pipeline/data/` → `workspaces/scripts/`（台本SoT）
- `audio_tts_v2/artifacts/` → `workspaces/audio/`（音声成果物）
- `commentary_02_srt2images_timeline/output/` → `workspaces/video/runs/`（動画run）
- `commentary_02_srt2images_timeline/input/` → `workspaces/video/input/`（動画入力）

## 追加: エピソードのリンク集（SoTではない）
- `workspaces/episodes/{CH}/{NNN}/` は「A→B→音声→SRT→run」を迷わず辿るための集約ビュー（symlink + manifest）。
  - 生成: `python3 scripts/episode_ssot.py materialize --channel CHxx --video NNN`

パス解決は `factory_common/paths.py` が正本です（`workspaces/` が実体化した時に自動で新パスへ寄ります）。

Stage2（scripts/audio/video/logs）の切替は `python scripts/ops/stage2_cutover_workspaces.py --run` が正本。

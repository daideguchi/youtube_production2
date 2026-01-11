# workspaces/

`workspaces/` は「SoT（正本データ）+ 生成物（成果物/ログ）」を集約する唯一の置き場です。

パス解決（コード/スクリプト側の正本）は `packages/factory_common/paths.py` です。

関連:
- `ssot/ops/OPS_REPO_DIRECTORY_SSOT.md`
- `ssot/ops/DATA_LAYOUT.md`

## サブディレクトリ（正本）
- `workspaces/planning/`: 企画CSV / Personas / Analytics
- `workspaces/scripts/`: 台本 SoT（`status.json`, `content/assembled*.md`, `audio_prep/`）
- `workspaces/audio/`: 音声 SoT（final wav/srt/log）
- `workspaces/video/`: 動画入力/Run（`input/`, `runs/`, `_capcut_drafts/`, `_state/`, `_archive/`）
- `workspaces/thumbnails/`: サムネ SoT（`projects.json`, `templates.json`, `assets/`）
- `workspaces/logs/`: 運用ログ集約（regression, agent_tasks, llm_usage など）
- `workspaces/research/`: 調査/ベンチ資料（ワークファイル）

## 追加: エピソードのリンク集（SoTではない）
- `workspaces/episodes/{CH}/{NNN}/` は「A→B→音声→SRT→run」を迷わず辿るための集約ビュー（symlink + manifest）。
  - 生成: `python3 scripts/episode_ssot.py materialize --channel CHxx --video NNN`

## CapCut Draft One-shot Pipeline (Channel/SRT agnostic)

Prereq:
- `source /Users/dd/youtube_master/.env` (GEMINI/OPENROUTER keys, etc.)
- `PYTHONPATH` is set by the script; no need to export manually.

Main command:
```
python3 tools/auto_capcut_run.py \
  --channel CH01 \
  --srt input/CH01_人生の道標/192.srt \
  --run-name jinsei192_vX \
  --title "人生の道標 192話 ～タイトル～" \
  --labels "序章,転機,対策,結び" \
  --template "" \
  --img-concurrency 3 \
  --nanobanana direct \
  --force
```
- Steps: run_pipeline → equal-split belt → CapCut draft insert → Title JSON injection.
- Presets: template/position/tone/character/belt_labels/opening_offset are auto-applied from `config/channel_presets.json`.
- SRT is auto-copied into the run dir; draft name defaults to `<srt_basename>_draft`.
- `--dry-run` to skip generation/draft ops (nanobanana=none) for quick preflight.
- `--suppress-warnings` default ON (DeprecationWarning).

Diffusion mode: switch with `--nanobanana direct|cli|mcp|none`.

After-run artifacts:
- `output/<run>/auto_run_info.json`: summary + cue counts + replacements log.
- Draft: `$HOME/Movies/CapCut/User Data/Projects/com.lveditor.draft/<srt_basename>_draft`

Partial image replacement:
```
python3 tools/replace_and_log.py \
  --run-dir output/jinsei192_vX \
  --draft "$HOME/Movies/CapCut/User Data/Projects/com.lveditor.draft/<draft_name>" \
  --indices 6,7,8
```
This logs replacements into `auto_run_info.json`.

Preset validation:
```
python3 tools/check_preset.py --all
```
Ensures active channels have capcut_template, belt_labels, and tone/character notes.

## CapCut Draft One-shot Pipeline (Channel/SRT agnostic)

Prereq:
- `.env` は **リポジトリ直下**が正本（GEMINI/OPENROUTER keys, etc.）。`.gemini_config` や credentials 配下に複製しない。
  - 推奨: `bash scripts/with_ytm_env.sh <cmd>`（`.env` を export して実行）
- `PYTHONPATH` is set by the script; no need to export manually.

Main command:
```
PYTHONPATH=".:packages" python3 -m commentary_02_srt2images_timeline.tools.auto_capcut_run \
  --channel CH01 \
  --srt workspaces/video/input/CH01_人生の道標/192.srt \
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
- `--suppress-warnings` default ON (DeprecationWarning).
- Diffusion mode is single-path: `--nanobanana direct` (Gemini via ImageClient) or `--nanobanana none` to skip. Legacy cli/mcp are removed to avoid routing mistakes.

After-run artifacts:
- `workspaces/video/runs/<run_id>/auto_run_info.json`: summary + cue counts + replacements log.
- Draft: `$HOME/Movies/CapCut/User Data/Projects/com.lveditor.draft/<srt_basename>_draft`

Partial image replacement:
```
PYTHONPATH=".:packages" python3 -m commentary_02_srt2images_timeline.tools.safe_image_swap \
  --run-dir workspaces/video/runs/jinsei192_vX \
  --draft "$HOME/Movies/CapCut/User Data/Projects/com.lveditor.draft/<draft_name>" \
  --indices 6,7,8 \
  --apply
```
※ `--apply` を外すと dry-run（書き込みなし）。

Preset validation:
```
PYTHONPATH=".:packages" python3 -m commentary_02_srt2images_timeline.tools.check_preset --all
```
Ensures active channels have capcut_template, belt_labels, and tone/character notes.

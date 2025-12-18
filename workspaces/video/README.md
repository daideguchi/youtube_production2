# workspaces/video/

Video pipeline SoT + generated artifacts (CapCut/Remotion). Do not commit artifacts.

- `runs/`: video run dirs (cues/images/belt_config/capcut_draft symlink, etc.)
- `input/`: mirrored audio inputs for CapCut (do not edit by hand)
- `_capcut_drafts/`: local CapCut draft fallback (NOT SoT; safe to archive duplicates)
  - cleanup: `python3 scripts/ops/archive_capcut_local_drafts.py --dry-run` → OKなら `--run`
- `_archive/`: archived runs (restore: `python3 scripts/ops/restore_video_runs.py ...`)
- `_state/`: state/manifest (audio sync status, etc.)

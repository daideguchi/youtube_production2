#!/usr/bin/env python3
"""
Validate channel preset integrity: capcut_template, belt_labels, tone/character notes.

Usage:
    python3 tools/check_preset.py --channel CH01
    python3 tools/check_preset.py --all
"""
import argparse
import json
import sys
from pathlib import Path

def _bootstrap_repo_root() -> Path:
    start = Path(__file__).resolve()
    cur = start if start.is_dir() else start.parent
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
    return cur


_BOOTSTRAP_REPO = _bootstrap_repo_root()
_PACKAGES_ROOT = _BOOTSTRAP_REPO / "packages"
for p in (_BOOTSTRAP_REPO, _PACKAGES_ROOT):
    p_str = str(p)
    if p_str not in sys.path:
        sys.path.insert(0, p_str)

from factory_common.paths import video_pkg_root  # noqa: E402

PROJECT_ROOT = video_pkg_root()
PRESET_PATH = PROJECT_ROOT / "config" / "channel_presets.json"


def load_presets():
    if not PRESET_PATH.exists():
        raise FileNotFoundError(f"Preset file not found: {PRESET_PATH}")
    data = json.loads(PRESET_PATH.read_text(encoding="utf-8"))
    return data.get("channels", {})


def validate_preset(ch_id, cfg):
    errors = []
    status = cfg.get("status", "active")
    if status != "active":
        # Skip strict checks for non-active channels (pending, etc.)
        return errors
    if not cfg.get("capcut_template"):
        errors.append("capcut_template missing")
    belt = cfg.get("belt", {})
    if belt.get("enabled") and "opening_offset" not in belt:
        errors.append("belt.opening_offset missing")
    if not cfg.get("belt_labels"):
        errors.append("belt_labels missing (equal-split labels)")
    # Optional guidance fields
    if not cfg.get("prompt_suffix"):
        errors.append("prompt_suffix missing (tone/char guidance)")
    if not cfg.get("tone_profile"):
        errors.append("tone_profile missing")
    if not cfg.get("character_note"):
        errors.append("character_note missing")
    return errors


def main():
    ap = argparse.ArgumentParser(description="Check channel preset integrity")
    ap.add_argument("--channel", help="Channel ID to check")
    ap.add_argument("--all", action="store_true", help="Check all channels")
    args = ap.parse_args()

    presets = load_presets()
    targets = [args.channel] if args.channel else list(presets.keys())
    if args.all:
        targets = list(presets.keys())

    failed = False
    for ch in targets:
        cfg = presets.get(ch)
        if not cfg:
            print(f"[{ch}] missing preset entry")
            failed = True
            continue
        errs = validate_preset(ch, cfg)
        if errs:
            failed = True
            print(f"[{ch}] ❌ " + "; ".join(errs))
        else:
            print(f"[{ch}] ✅ ok")

    if failed:
        exit(1)


if __name__ == "__main__":
    main()

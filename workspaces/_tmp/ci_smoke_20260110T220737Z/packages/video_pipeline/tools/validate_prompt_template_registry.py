#!/usr/bin/env python3
"""
Validate that `channel_presets.json` prompt_template entries are registered in `template_registry.json`.

Exit code:
  - 0: all channels reference registered prompt templates
  - 1: at least one channel references a missing/empty template

Usage:
  PYTHONPATH=".:packages" python3 -m video_pipeline.tools.validate_prompt_template_registry
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from _bootstrap import bootstrap

from factory_common.paths import video_pkg_root

bootstrap(load_env=False)


def _default_presets_path() -> Path:
    root = video_pkg_root()
    return root / "config" / "channel_presets.json"


def _default_registry_path() -> Path:
    root = video_pkg_root()
    return root / "config" / "template_registry.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--presets", default=str(_default_presets_path()), help="Path to channel_presets.json")
    ap.add_argument("--registry", default=str(_default_registry_path()), help="Path to template_registry.json")
    args = ap.parse_args()

    presets_path = Path(args.presets).expanduser().resolve()
    registry_path = Path(args.registry).expanduser().resolve()

    missing = []
    try:
        presets = json.loads(presets_path.read_text(encoding="utf-8")).get("channels", {})
    except Exception as exc:
        print(f"❌ failed to load channel_presets.json: {exc}")
        return 1
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8")).get("templates", [])
    except Exception as exc:
        print(f"❌ failed to load template_registry.json: {exc}")
        return 1

    registry_ids = {Path(t["id"]).name for t in registry}

    for ch, cfg in presets.items():
        tpl = cfg.get("prompt_template")
        if not tpl:
            missing.append((ch, "<empty>"))
            continue
        name = Path(tpl).name
        if name not in registry_ids:
            missing.append((ch, tpl))

    if missing:
        print("❌ missing templates in registry:")
        for ch, tpl in missing:
            print(f"  - {ch}: {tpl}")
        return 1

    print("✅ channel_presets templates are registered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

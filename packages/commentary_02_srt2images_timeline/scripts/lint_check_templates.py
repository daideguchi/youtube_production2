#!/usr/bin/env python3
"""
Lint: ensure channel_presets prompt_template is registered in template_registry.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from factory_common.paths import video_pkg_root

PROJECT_ROOT = video_pkg_root()
PRESETS = PROJECT_ROOT / "config" / "channel_presets.json"
REGISTRY = PROJECT_ROOT / "config" / "template_registry.json"


def main() -> int:
    missing = []
    try:
        presets = json.loads(PRESETS.read_text(encoding="utf-8")).get("channels", {})
    except Exception as exc:
        print(f"❌ failed to load channel_presets.json: {exc}")
        return 1
    try:
        registry = json.loads(REGISTRY.read_text(encoding="utf-8")).get("templates", [])
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
    sys.exit(main())

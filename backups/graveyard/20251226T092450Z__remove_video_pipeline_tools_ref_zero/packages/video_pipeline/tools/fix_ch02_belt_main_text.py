#!/usr/bin/env python3
"""
Fix CH02 CapCut drafts: set the main belt text (belt_main_text) from belt_config.json.

Why:
- Some templates store text material `content` as a dict ({"text": ...}) rather than a JSON string.
- Previous belt postprocess assumed JSON-string and failed, leaving the template placeholder text.

This script patches BOTH:
- draft_content.json
- draft_info.json

…in each CapCut draft directory.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


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

from factory_common.paths import video_runs_root

CAPCUT_DRAFT_ROOT = Path(
    os.getenv("CAPCUT_DRAFT_ROOT")
    or (Path.home() / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft")
)
RUN_ROOT = video_runs_root()


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, data: Dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _get_belt_text_from_belt_config(belt_config_path: Path) -> Optional[str]:
    data = _load_json(belt_config_path)
    belts = data.get("belts")
    if isinstance(belts, list) and belts:
        first = belts[0]
        if isinstance(first, dict):
            text = first.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
    main_title = data.get("main_title")
    if isinstance(main_title, str) and main_title.strip():
        return main_title.strip()
    return None


def _normalize_text_content(content: Any) -> Tuple[Dict[str, Any], str]:
    """
    Return (content_obj, mode) where mode controls how to re-serialize:
    - 'dict': keep as dict
    - 'json_str': keep as JSON string
    - 'plain_str': keep as plain string (draft_info.json template style)
    """
    if isinstance(content, dict):
        return content, "dict"
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except Exception:
            return {"text": content}, "plain_str"
        if not isinstance(parsed, dict):
            return {"text": content}, "plain_str"
        return parsed, "json_str"
    return {"text": ""}, "dict"


def _set_material_text(mat: Dict[str, Any], new_text: str) -> bool:
    content_obj, mode = _normalize_text_content(mat.get("content"))
    old_text = content_obj.get("text")
    content_obj["text"] = new_text
    if mode == "dict":
        mat["content"] = content_obj
    elif mode == "json_str":
        mat["content"] = json.dumps(content_obj, ensure_ascii=False)
    else:
        mat["content"] = new_text
    # keep base_content in sync if present
    if "base_content" in mat:
        mat["base_content"] = new_text
    return old_text != new_text


def _patch_one_draft_json(path: Path, belt_text: str) -> bool:
    data = _load_json(path)
    mats = data.get("materials", {})
    texts = None
    if isinstance(mats, dict):
        texts = mats.get("texts")
    if not isinstance(texts, list):
        return False

    changed_any = False
    for t in texts:
        if not isinstance(t, dict):
            continue
        if t.get("id") != "belt_main_text":
            continue
        changed_any |= _set_material_text(t, belt_text)
        # keep name stable
        if not t.get("name"):
            t["name"] = "belt_main_text"

    if changed_any:
        _save_json(path, data)
    return changed_any


def _find_target_draft_dirs(pattern: re.Pattern[str]) -> list[Path]:
    out: list[Path] = []
    for child in CAPCUT_DRAFT_ROOT.iterdir():
        if not child.is_dir():
            continue
        if not pattern.match(child.name):
            continue
        out.append(child)
    return sorted(out, key=lambda p: p.name)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--draft-regex",
        default=r"^CH02-(014|019|020|021|022|023|024|025|026|027|028|029|030|031|032|033)_regen_\d{8}_\d{6}_draft$",
        help="Regex to select CapCut draft directories under the CapCut draft root.",
    )
    args = parser.parse_args()
    pattern = re.compile(args.draft_regex)

    draft_dirs = _find_target_draft_dirs(pattern)
    if not draft_dirs:
        raise SystemExit(f"No draft dirs matched: {args.draft_regex}")

    changed = 0
    skipped = 0
    for draft_dir in draft_dirs:
        run_name = draft_dir.name.removesuffix("_draft")
        belt_config_path = RUN_ROOT / run_name / "belt_config.json"
        if not belt_config_path.exists():
            print(f"SKIP (no belt_config): {draft_dir.name}")
            skipped += 1
            continue
        belt_text = _get_belt_text_from_belt_config(belt_config_path)
        if not belt_text:
            print(f"SKIP (no belt text): {draft_dir.name}")
            skipped += 1
            continue

        any_changed = False
        for fname in ("draft_content.json", "draft_info.json"):
            p = draft_dir / fname
            if not p.exists():
                continue
            any_changed |= _patch_one_draft_json(p, belt_text)

        if any_changed:
            print(f"OK  {draft_dir.name}: {belt_text}")
            changed += 1
        else:
            print(f"NOOP {draft_dir.name}: {belt_text}")

    print(f"\nDone. updated={changed}, skipped={skipped}, total={len(draft_dirs)}")


if __name__ == "__main__":
    main()

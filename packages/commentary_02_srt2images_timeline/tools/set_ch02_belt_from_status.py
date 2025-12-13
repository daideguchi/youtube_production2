#!/usr/bin/env python3
"""
Set CH02 main belt text from script status.json (sheet_title/topic), and patch CapCut drafts.

Goal:
- Keep belt layer styling EXACTLY as the template (we only change the text string).
- Avoid per-run LLM guessing for belt text; use the script's canonical title/topic from SSOT/status.json.

What it updates:
- CapCut drafts (draft_content.json + draft_info.json) under the CapCut draft root
- Optionally, the corresponding run_dir belt_config.json main_title for consistency
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory_common.paths import status_path as script_status_path
from factory_common.paths import video_runs_root

CAPCUT_DRAFT_ROOT = Path(
    os.getenv("CAPCUT_DRAFT_ROOT")
    or (Path.home() / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft")
)
RUN_ROOT = video_runs_root()


DEFAULT_TARGETS = [
    "014",
    "019", "020", "021", "022", "023", "024", "025", "026", "027", "028", "029", "030", "031", "032", "033",
]


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, data: Dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_text_content(content: Any) -> Tuple[Dict[str, Any], str]:
    if isinstance(content, dict):
        return content, "dict"
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except Exception:
            # draft_info.json uses plain string content (not JSON)
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
        # plain_str (draft_info.json)
        mat["content"] = new_text
    if "base_content" in mat:
        mat["base_content"] = new_text
    return old_text != new_text


def _patch_draft_belt_text(draft_dir: Path, belt_text: str) -> bool:
    changed_any = False
    for fname in ("draft_content.json", "draft_info.json"):
        p = draft_dir / fname
        if not p.exists():
            continue
        data = _load_json(p)
        mats = data.get("materials", {})
        texts = mats.get("texts") if isinstance(mats, dict) else None
        if not isinstance(texts, list):
            continue
        changed = False
        for t in texts:
            if not isinstance(t, dict):
                continue
            if t.get("id") != "belt_main_text":
                continue
            changed |= _set_material_text(t, belt_text)
            if not t.get("name"):
                t["name"] = "belt_main_text"
        if changed:
            _save_json(p, data)
            changed_any = True
    return changed_any


def _extract_bracket_topic(sheet_title: str) -> Optional[str]:
    m = re.search(r"【([^】]+)】", sheet_title or "")
    if not m:
        return None
    topic = (m.group(1) or "").strip()
    return topic or None


def _derive_topic_from_status(channel: str, video: str) -> str:
    path = script_status_path(channel, video)
    data = _load_json(path)
    meta = data.get("metadata", {}) if isinstance(data, dict) else {}
    sheet_title = meta.get("sheet_title")
    if isinstance(sheet_title, str) and sheet_title.strip():
        topic = _extract_bracket_topic(sheet_title)
        if topic:
            return topic
    # fallback: try long 'title' field (intro-style), extract a short keyword
    title = meta.get("title")
    if isinstance(title, str) and title.strip():
        # Heuristic: pick a known phrase if present
        if "燃え尽き" in title:
            return "静かな燃え尽き"
        if "優しさの疲労" in title:
            return "優しさの疲労"
        if "刃" in title and "丸め" in title:
            return "前向きの刃を丸める"
        if "刃" in title:
            return "言葉の刃"
        # otherwise, take first sentence fragment
        first = re.split(r"[。！？]", title.strip())[0]
        first = re.sub(r"\s+", "", first)
        return first[:14] if first else "CH02"
    return "CH02"


def _find_draft_dirs(channel: str, videos: list[str]) -> list[Path]:
    out: list[Path] = []
    for video in videos:
        pat = re.compile(rf"^{re.escape(channel)}-{re.escape(video)}_regen_\d{{8}}_\d{{6}}_draft$")
        for child in CAPCUT_DRAFT_ROOT.iterdir():
            if child.is_dir() and pat.match(child.name):
                out.append(child)
    return sorted(out, key=lambda p: p.name)


def _patch_run_belt_config(channel: str, draft_dir_name: str, belt_text: str) -> bool:
    run_name = draft_dir_name.removesuffix("_draft")
    belt_path = RUN_ROOT / run_name / "belt_config.json"
    if not belt_path.exists():
        return False
    data = _load_json(belt_path)
    if not isinstance(data, dict):
        return False
    before = data.get("main_title")
    data["main_title"] = belt_text
    _save_json(belt_path, data)
    return before != belt_text


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="CH02")
    ap.add_argument("--videos", default=",".join(DEFAULT_TARGETS), help="Comma-separated video numbers (e.g., 014,019,020)")
    ap.add_argument("--update-run-belt-config", action="store_true", help="Also update run_dir belt_config.json main_title")
    args = ap.parse_args()

    channel = args.channel
    videos = [v.strip() for v in args.videos.split(",") if v.strip()]
    if not videos:
        raise SystemExit("videos empty")

    draft_dirs = _find_draft_dirs(channel, videos)
    if not draft_dirs:
        raise SystemExit("No matching draft dirs found")

    changed = 0
    for d in draft_dirs:
        # parse video from name: CH02-014_regen_..._draft
        m = re.match(rf"^{re.escape(channel)}-(\d{{3}})_regen_", d.name)
        if not m:
            continue
        video = m.group(1)
        belt_text = _derive_topic_from_status(channel, video)
        did = _patch_draft_belt_text(d, belt_text)
        if args.update_run_belt_config:
            _patch_run_belt_config(channel, d.name, belt_text)
        if did:
            print(f"OK  {d.name}: {belt_text}")
            changed += 1
        else:
            print(f"NOOP {d.name}: {belt_text}")

    print(f"\nDone. updated={changed}, total={len(draft_dirs)}")


if __name__ == "__main__":
    main()

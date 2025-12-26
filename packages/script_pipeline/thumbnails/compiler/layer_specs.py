#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from functools import lru_cache
from typing import Any, Dict, Optional, Tuple

import json
import yaml

from factory_common import paths as fpaths


@dataclass(frozen=True)
class LayerSpecRef:
    spec_id: str
    kind: str  # "image_prompts" | "text_layout"
    version: int
    path: str  # repo-relative
    name: str


def _templates_json_path() -> Path:
    return fpaths.thumbnails_root() / "templates.json"


def load_layer_specs_index() -> Dict[str, Any]:
    """
    Load layer spec registry from `workspaces/thumbnails/templates.json`.

    The registry is treated as configuration, not as a hard requirement: callers
    should handle missing keys gracefully.
    """
    path = _templates_json_path()
    payload = json.loads(path.read_text(encoding="utf-8"))
    layer_specs = payload.get("layer_specs")
    if not isinstance(layer_specs, dict):
        return {"defaults": {}, "registry": {}}
    defaults = layer_specs.get("defaults")
    registry = layer_specs.get("registry")
    return {
        "defaults": defaults if isinstance(defaults, dict) else {},
        "registry": registry if isinstance(registry, dict) else {},
    }


def resolve_channel_layer_spec_ids(channel_code: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Resolve layer spec ids for a channel:
    - channel-level override in templates.json (channels[CHxx].layer_specs)
    - then layer_specs.defaults
    """
    channel = str(channel_code).upper().strip()
    templates_path = _templates_json_path()
    payload = json.loads(templates_path.read_text(encoding="utf-8"))

    channels = payload.get("channels") if isinstance(payload, dict) else None
    channel_doc = channels.get(channel) if isinstance(channels, dict) else None
    ch_layer = channel_doc.get("layer_specs") if isinstance(channel_doc, dict) else None
    if isinstance(ch_layer, dict):
        img_id = ch_layer.get("image_prompts_id")
        txt_id = ch_layer.get("text_layout_id")
        return (str(img_id).strip() if isinstance(img_id, str) and str(img_id).strip() else None,
                str(txt_id).strip() if isinstance(txt_id, str) and str(txt_id).strip() else None)

    index = load_layer_specs_index()
    defaults = index.get("defaults") if isinstance(index, dict) else {}
    if not isinstance(defaults, dict):
        return (None, None)
    img_id = defaults.get("image_prompts_id")
    txt_id = defaults.get("text_layout_id")
    return (
        str(img_id).strip() if isinstance(img_id, str) and str(img_id).strip() else None,
        str(txt_id).strip() if isinstance(txt_id, str) and str(txt_id).strip() else None,
    )


def resolve_layer_spec_ref(spec_id: str) -> LayerSpecRef:
    index = load_layer_specs_index()
    registry = index.get("registry") if isinstance(index, dict) else None
    if not isinstance(registry, dict):
        raise KeyError("layer_specs.registry is missing")
    entry = registry.get(spec_id)
    if not isinstance(entry, dict):
        raise KeyError(f"unknown layer spec id: {spec_id}")

    kind = str(entry.get("kind") or "").strip()
    path = str(entry.get("path") or "").strip()
    name = str(entry.get("name") or spec_id).strip() or spec_id
    version = entry.get("version")
    if not isinstance(version, int):
        version = 0
    if not kind or not path:
        raise ValueError(f"invalid layer spec entry for {spec_id}: {entry}")
    return LayerSpecRef(spec_id=spec_id, kind=kind, version=version, path=path, name=name)


@lru_cache(maxsize=32)
def _load_layer_spec_yaml_cached(spec_id: str, mtime: float) -> Dict[str, Any]:
    ref = resolve_layer_spec_ref(spec_id)
    path = fpaths.repo_root() / ref.path
    if not path.exists():
        raise FileNotFoundError(f"layer spec not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"invalid layer spec payload: {path}")
    return data


def load_layer_spec_yaml(spec_id: str) -> Dict[str, Any]:
    ref = resolve_layer_spec_ref(spec_id)
    path = fpaths.repo_root() / ref.path
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        mtime = 0.0
    return _load_layer_spec_yaml_cached(spec_id, mtime)


def find_image_prompt_for_video(spec: Dict[str, Any], video_id: str) -> Optional[str]:
    vid = str(video_id).strip()
    items = spec.get("items")
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("video_id") or "").strip() != vid:
            continue
        prompt = item.get("prompt_ja")
        if isinstance(prompt, str) and prompt.strip():
            return prompt.strip()
    return None


def find_text_layout_item_for_video(spec: Dict[str, Any], video_id: str) -> Optional[Dict[str, Any]]:
    vid = str(video_id).strip()
    items = spec.get("items")
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("video_id") or "").strip() != vid:
            continue
        return item
    return None

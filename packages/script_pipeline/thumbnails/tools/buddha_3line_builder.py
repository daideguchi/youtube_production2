#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml
from PIL import Image

from factory_common import paths as fpaths
from script_pipeline.thumbnails.compiler.compile_buddha_3line import (
    ThumbText,
    compose_buddha_3line,
    resolve_font_path,
)
from script_pipeline.tools.optional_fields_registry import FIELD_KEYS


def _normalize_channel(channel: str) -> str:
    return str(channel or "").strip().upper()


def _normalize_video(video: str) -> str:
    raw = str(video or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        raise ValueError(f"invalid video: {video}")
    return digits.zfill(3)


def _pick_video_number(row: Dict[str, str]) -> str:
    for key in ("動画番号", "video", "Video", "No.", "No"):
        v = (row.get(key) or "").strip()
        if v:
            return _normalize_video(v)
    raise KeyError("Could not find video number column in planning CSV row")


def _pick_text_from_row(row: Dict[str, str]) -> ThumbText:
    upper_col = FIELD_KEYS.get("thumbnail_upper", "サムネタイトル上")
    title_col = FIELD_KEYS.get("thumbnail_title", "サムネタイトル")
    lower_col = FIELD_KEYS.get("thumbnail_lower", "サムネタイトル下")
    return ThumbText(
        upper=(row.get(upper_col) or "").strip(),
        title=(row.get(title_col) or "").strip(),
        lower=(row.get(lower_col) or "").strip(),
    )


def _load_stylepack(channel: str) -> Dict[str, Any]:
    ch = _normalize_channel(channel)
    stylepacks_dir = fpaths.thumbnails_root() / "compiler" / "stylepacks"
    if not stylepacks_dir.exists():
        raise FileNotFoundError(f"Missing stylepacks dir: {stylepacks_dir}")

    candidates = sorted(stylepacks_dir.glob(f"{ch}_*.yaml"))
    if not candidates:
        candidates = sorted(stylepacks_dir.glob("*.yaml"))

    for p in candidates:
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        if str(data.get("channel", "")).upper() == ch:
            data["_stylepack_path"] = str(p)
            return data
    raise FileNotFoundError(f"No stylepack found for {channel} in {stylepacks_dir}")


def _load_thumbnail_projects_path() -> Path:
    return fpaths.thumbnails_root() / "projects.json"


def _load_thumbnail_projects() -> Dict[str, Any]:
    path = _load_thumbnail_projects_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": 1, "updated_at": None, "projects": []}


def _write_thumbnail_projects(doc: Dict[str, Any]) -> None:
    path = _load_thumbnail_projects_path()
    doc["version"] = int(doc.get("version") or 1)
    doc["updated_at"] = datetime.now(timezone.utc).isoformat()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def upsert_compiler_variant(
    *,
    channel: str,
    video: str,
    title: Optional[str],
    build_id: str,
    image_rel_path: str,
    label: Optional[str] = None,
    status: str = "review",
    select: bool = True,
) -> None:
    doc = _load_thumbnail_projects()
    projects = doc.get("projects")
    if not isinstance(projects, list):
        projects = []
        doc["projects"] = projects

    now = datetime.now(timezone.utc).isoformat()
    project: Optional[Dict[str, Any]] = None
    for entry in projects:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("channel") or "").upper() == channel and str(entry.get("video") or "") == video:
            project = entry
            break
    if project is None:
        project = {
            "channel": channel,
            "video": video,
            "variants": [],
            "status": status,
            "status_updated_at": now,
            "updated_at": now,
        }
        projects.append(project)
    elif not (isinstance(project.get("status"), str) and str(project.get("status") or "").strip()):
        # Ensure UI-friendly status for projects created by offline builders.
        project["status"] = status
        project["status_updated_at"] = now

    if title:
        project["title"] = title

    variants = project.get("variants")
    if not isinstance(variants, list):
        variants = []
        project["variants"] = variants

    variant_id = f"compiler::{build_id}"
    label_value = label or f"文字サムネ（{build_id}）"

    for v in variants:
        if isinstance(v, dict) and str(v.get("id") or "") == variant_id:
            v["label"] = label_value
            v["status"] = status
            v["image_url"] = f"/thumbnails/assets/{image_rel_path}"
            v["image_path"] = image_rel_path
            v["updated_at"] = now
            project["updated_at"] = now
            if select:
                project["selected_variant_id"] = variant_id
            _write_thumbnail_projects(doc)
            return

    variants.insert(
        0,
        {
            "id": variant_id,
            "label": label_value,
            "status": status,
            "image_url": f"/thumbnails/assets/{image_rel_path}",
            "image_path": image_rel_path,
            "notes": None,
            "tags": ["compiled"],
            "prompt": None,
            "created_at": now,
            "updated_at": now,
        },
    )
    project["updated_at"] = now
    if select or not project.get("selected_variant_id"):
        project["selected_variant_id"] = variant_id
    _write_thumbnail_projects(doc)


def read_planning_rows(channel: str) -> List[Dict[str, str]]:
    csv_path = fpaths.channels_csv_path(channel)
    if not csv_path.exists():
        raise FileNotFoundError(f"Missing planning CSV: {csv_path}")
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


def build_buddha_3line(
    *,
    channel: str,
    videos: List[str],
    base_image_path: Path,
    build_id: str,
    font_path: Optional[str] = None,
    flip_base: bool = True,
    impact: bool = True,
    belt_override: Optional[bool] = None,
    select_variant: bool = True,
) -> List[Path]:
    """
    Compose `buddha_3line` thumbnails for a list of videos and register them as compiler variants.

    Output:
      workspaces/thumbnails/assets/{CH}/{NNN}/compiler/{build_id}/out_01.png
    """
    ch = _normalize_channel(channel)
    vids = [_normalize_video(v) for v in videos]

    stylepack = _load_stylepack(ch)
    rows = read_planning_rows(ch)
    row_by_video: Dict[str, Dict[str, str]] = {}
    for row in rows:
        try:
            v = _pick_video_number(row)
        except Exception:
            continue
        row_by_video[v] = row

    base_path = Path(base_image_path).expanduser().resolve()
    if not base_path.exists():
        raise FileNotFoundError(f"Base image not found: {base_path}")

    resolved_font_path = resolve_font_path(font_path)

    wrote: List[Path] = []
    for vid in vids:
        row = row_by_video.get(vid)
        if not row:
            raise KeyError(f"planning CSV row not found for {ch}-{vid}")
        text = _pick_text_from_row(row)
        if not (text.upper or text.title or text.lower):
            raise ValueError(f"thumbnail text empty for {ch}-{vid}")

        out_dir = fpaths.thumbnail_assets_dir(ch, vid) / "compiler" / build_id
        out_img_path = out_dir / "out_01.png"
        out_meta_path = out_dir / "meta.json"
        out_dir.mkdir(parents=True, exist_ok=True)

        img = compose_buddha_3line(
            base_image_path=base_path,
            stylepack=stylepack,
            text=text,
            font_path=resolved_font_path,
            flip_base=flip_base,
            impact=impact,
            belt_override=belt_override,
        )
        img.convert("RGB").save(out_img_path, format="PNG", optimize=True)

        meta = {
            "schema": "ytm.thumbnail.compiler.build.v1",
            "built_at": datetime.now(timezone.utc).isoformat(),
            "channel": ch,
            "video": vid,
            "stylepack_id": stylepack.get("id"),
            "stylepack_path": stylepack.get("_stylepack_path"),
            "base_image": str(base_path),
            "flip_base": flip_base,
            "impact": impact,
            "belt_enabled": belt_override,
            "text": {"upper": text.upper, "title": text.title, "lower": text.lower},
            "output": {"image": str(out_img_path)},
        }
        out_meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        wrote.append(out_img_path)

        rel = f"{ch}/{vid}/compiler/{build_id}/out_01.png"
        upsert_compiler_variant(
            channel=ch,
            video=vid,
            title=None,
            build_id=build_id,
            image_rel_path=rel,
            status="review",
            select=select_variant,
        )

    return wrote

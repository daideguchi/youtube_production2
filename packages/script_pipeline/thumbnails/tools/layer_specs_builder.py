#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import copy
import json
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from PIL import Image, ImageDraw, ImageOps

from factory_common import paths as fpaths
from factory_common.image_client import ImageClient
from script_pipeline.thumbnails.compiler.layer_specs import (
    find_text_layout_item_for_video,
    load_image_prompts_v3_typed,
    load_layer_spec_yaml,
    load_text_layout_v3_typed,
    resolve_channel_layer_spec_ids,
)
from script_pipeline.thumbnails.compiler.layer_specs_schema_v3 import ImagePromptsSpecV3, TextLayoutSpecV3
from script_pipeline.thumbnails.layers.image_layer import (
    BgEnhanceParams,
    crop_resize_to_16x9,
    composited_portrait_path,
    enhanced_bg_path,
    find_existing_portrait,
    generate_background_with_retries,
    resolve_background_source,
    suppressed_center_region_path,
)
from script_pipeline.thumbnails.layers.text_layer import compose_text_to_png
from script_pipeline.thumbnails.io_utils import PngOutputMode, save_png_atomic
from script_pipeline.thumbnails.thumb_spec import extract_normalized_override_leaf, load_thumb_spec
from script_pipeline.tools import planning_store


@dataclass(frozen=True)
class BuildTarget:
    channel: str
    video: str  # 3-digit

    @property
    def video_id(self) -> str:
        return f"{self.channel}-{self.video}"


def _normalize_channel(channel: str) -> str:
    return str(channel or "").strip().upper()


def _normalize_video(video: str) -> str:
    raw = str(video or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        raise ValueError(f"invalid video: {video}")
    return digits.zfill(3)

def _load_planning_copy(channel: str, video: str) -> Dict[str, str]:
    """
    Load copy fields from planning CSV.

    Returns:
      {upper, title, lower}
    """
    ch = _normalize_channel(channel)
    v = _normalize_video(video)
    try:
        rows = planning_store.get_rows(ch, force_refresh=False)
    except Exception:
        return {}
    for row in rows:
        try:
            row_v = _normalize_video(row.video_number or "")
        except Exception:
            continue
        if row_v != v:
            continue
        raw = row.raw if isinstance(row.raw, dict) else {}
        upper = str(raw.get("サムネタイトル上") or "").strip()
        title = str(raw.get("サムネタイトル") or "").strip()
        lower = str(raw.get("サムネタイトル下") or "").strip()

        # CH01 など: サムネタイトルに3行をまとめて入れる運用を許容
        if title and not upper and not lower:
            decoded = str(title).replace("\\n", "\n")
            if "\n" in decoded:
                lines = [ln.strip() for ln in decoded.splitlines() if ln.strip()]
                if len(lines) >= 3:
                    upper, title, lower = lines[0], lines[1], lines[2]
                elif len(lines) == 2:
                    upper, title = lines[0], lines[1]
        return {"upper": upper, "title": title, "lower": lower}
    return {}


def _planning_value_for_slot(slot_name: str, copy: Dict[str, str]) -> str:
    name = str(slot_name or "").strip().lower()
    if name in {"line1", "upper", "top"}:
        return str(copy.get("upper") or "").strip()
    if name in {"line2", "title", "main"}:
        return str(copy.get("title") or "").strip()
    if name in {"line3", "lower", "accent"}:
        return str(copy.get("lower") or "").strip()
    return ""


def iter_targets_from_layer_specs(channel: str, videos: Optional[List[str]]) -> List[BuildTarget]:
    """
    Resolve build targets for a channel using layer_specs configuration.
    If `videos` is None/empty, derive targets from image_prompts items.
    """
    ch = _normalize_channel(channel)
    if videos:
        return [BuildTarget(channel=ch, video=_normalize_video(v)) for v in videos]

    img_id, txt_id = resolve_channel_layer_spec_ids(ch)
    if not img_id or not txt_id:
        raise RuntimeError(f"layer_specs not configured for channel: {ch}")

    vids: List[str] = []
    img_spec = load_image_prompts_v3_typed(img_id)
    for item in img_spec.items:
        vid = str(item.video_id).strip()
        if not vid.startswith(f"{ch}-"):
            continue
        suffix = vid.split("-", 1)[1] if "-" in vid else ""
        if suffix and suffix.isdigit():
            vids.append(suffix.zfill(3))
    if not vids:
        raise RuntimeError(f"no video targets found in layer spec for channel={ch}")
    return [BuildTarget(channel=ch, video=v) for v in sorted(set(vids))]


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


def upsert_fs_variant(
    *,
    channel: str,
    video: str,
    title: Optional[str],
    image_rel_path: str,
    label: str,
    status: str = "review",
) -> None:
    """
    Register a filesystem-backed variant (image_path points under thumbnails/assets).

    Safe behavior:
    - de-dup by image_path
    - do not overwrite selected_variant_id if already set
    """
    doc = _load_thumbnail_projects()
    projects = doc.get("projects")
    if not isinstance(projects, list):
        projects = []
        doc["projects"] = projects

    project: Optional[Dict[str, Any]] = None
    for entry in projects:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("channel") or "").upper() == channel and str(entry.get("video") or "") == video:
            project = entry
            break
    if project is None:
        project = {"channel": channel, "video": video, "variants": []}
        projects.append(project)

    if title:
        project["title"] = title
    if status:
        project["status"] = status
        project["status_updated_at"] = datetime.now(timezone.utc).isoformat()
    project.setdefault("variants", [])
    if not isinstance(project["variants"], list):
        project["variants"] = []

    for variant in project["variants"]:
        if isinstance(variant, dict) and str(variant.get("image_path") or "") == image_rel_path:
            variant["label"] = label
            variant["status"] = status
            variant["updated_at"] = datetime.now(timezone.utc).isoformat()
            project["updated_at"] = datetime.now(timezone.utc).isoformat()
            _write_thumbnail_projects(doc)
            return

    variant_id = f"fs::{channel.lower()}_{video}_{Path(image_rel_path).stem}"
    project["variants"].insert(
        0,
        {
            "id": variant_id,
            "label": label,
            "status": status,
            "image_url": f"/thumbnails/assets/{image_rel_path}",
            "image_path": image_rel_path,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    project["updated_at"] = datetime.now(timezone.utc).isoformat()
    if not project.get("selected_variant_id"):
        project["selected_variant_id"] = variant_id
    _write_thumbnail_projects(doc)


def _resolve_model_key_from_templates(channel: str) -> Optional[str]:
    templates_path = fpaths.thumbnails_root() / "templates.json"
    try:
        payload = json.loads(templates_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    channels = payload.get("channels") if isinstance(payload, dict) else None
    channel_doc = channels.get(channel) if isinstance(channels, dict) else None
    if not isinstance(channel_doc, dict):
        return None
    default_id = str(channel_doc.get("default_template_id") or "").strip()
    templates = channel_doc.get("templates") if isinstance(channel_doc.get("templates"), list) else []
    for tpl in templates:
        if not isinstance(tpl, dict):
            continue
        if default_id and str(tpl.get("id") or "").strip() != default_id:
            continue
        key = str(tpl.get("image_model_key") or "").strip()
        if key:
            return key
    return None


def _load_compiler_defaults_from_templates(channel: str) -> Dict[str, Any]:
    templates_path = fpaths.thumbnails_root() / "templates.json"
    try:
        payload = json.loads(templates_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    channels = payload.get("channels") if isinstance(payload, dict) else None
    channel_doc = channels.get(channel) if isinstance(channels, dict) else None
    defaults = channel_doc.get("compiler_defaults") if isinstance(channel_doc, dict) else None
    return defaults if isinstance(defaults, dict) else {}


def _resolve_title_from_specs(
    *,
    channel: str,
    video_id: str,
    image_spec: Optional[ImagePromptsSpecV3],
    text_spec: Optional[TextLayoutSpecV3],
) -> Optional[str]:
    if text_spec is not None:
        for item in text_spec.items:
            if str(item.video_id).strip() == str(video_id).strip():
                t = str(item.title).strip()
                if t:
                    return t
    if image_spec is not None:
        for item in image_spec.items:
            if str(item.video_id).strip() == str(video_id).strip():
                t = str(item.title).strip()
                if t:
                    return t
    return None


def _load_planning_image_prompt(channel: str, video: str) -> str:
    """
    Load per-video image prompt from Planning CSV (best-effort).

    This is a fallback when layer_specs image_prompts does not include the video_id.
    """
    ch = _normalize_channel(channel)
    v = _normalize_video(video)
    try:
        rows = planning_store.get_rows(ch, force_refresh=False)
    except Exception:
        return ""

    keys = [
        "サムネ画像プロンプト（URL・テキスト指示込み）",
        # Planning CSV common columns (channel-dependent).
        "AI向け画像生成プロンプト (背景用)",
        "AI向け画像生成プロンプト（背景用）",
        "サムネ用DALL-Eプロンプト（URL・テキスト指示込み）",
        "DALL-Eプロンプト（URL・テキスト指示込み）",
        "サムネ画像プロンプト",
        "サムネ画像プロンプト（URL）",
        "thumbnail_prompt",
        "thumbnail_image_prompt",
    ]

    for row in rows:
        try:
            row_v = _normalize_video(row.video_number or "")
        except Exception:
            continue
        if row_v != v:
            continue
        raw = row.raw if isinstance(row.raw, dict) else {}
        for k in keys:
            val = raw.get(k)
            if isinstance(val, str) and val.strip():
                return val.strip()
        break
    return ""


def _default_text_template_id(text_layout_spec: Dict[str, Any]) -> str:
    templates = text_layout_spec.get("templates") if isinstance(text_layout_spec, dict) else None
    if not isinstance(templates, dict) or not templates:
        return ""
    keys = [str(k).strip() for k in templates.keys() if str(k).strip()]
    keys.sort()
    return keys[0] if keys else ""


def _ensure_text_layout_item(
    *,
    text_layout_spec: Dict[str, Any],
    video_id: str,
    template_id: str,
    title: str,
) -> Dict[str, Any]:
    """
    Ensure `text_layout_spec.items` contains `video_id` (do not mutate the input dict).

    The layer_specs YAML is cached; mutating it would pollute subsequent builds.
    """
    existing = find_text_layout_item_for_video(text_layout_spec, video_id) if isinstance(text_layout_spec, dict) else None
    if isinstance(existing, dict):
        return text_layout_spec

    templates = text_layout_spec.get("templates") if isinstance(text_layout_spec, dict) else None
    tpl = templates.get(template_id) if isinstance(templates, dict) else None
    slots_payload = tpl.get("slots") if isinstance(tpl, dict) else None
    slot_keys = [
        str(k).strip()
        for k in (slots_payload.keys() if isinstance(slots_payload, dict) else [])
        if isinstance(k, str) and str(k).strip()
    ]
    if not slot_keys:
        slot_keys = ["main"]
    synthetic_item = {
        "video_id": str(video_id).strip(),
        "title": str(title).strip() or str(video_id).strip(),
        "template_id": str(template_id).strip(),
        "text": {k: "" for k in slot_keys},
    }

    spec_copy = copy.deepcopy(text_layout_spec)
    items = spec_copy.get("items")
    if not isinstance(items, list):
        items = []
        spec_copy["items"] = items
    items.append(synthetic_item)
    return spec_copy


def _sanitize_prompt_for_generation(*, channel: str, prompt: str) -> str:
    """
    Avoid giving the image model literal copy strings that it might render into the image.
    """
    p = str(prompt or "").strip()
    if not p:
        return ""
    ch = _normalize_channel(channel)
    if ch == "CH26":
        lines: List[str] = []
        for raw in p.splitlines():
            s = raw.strip()
            if s.startswith("テーマ:") or s.startswith("テーマ："):
                continue
            if s.startswith("人物:") or s.startswith("人物："):
                # CH26は人物を別レイヤで合成する運用（本人肖像を使用）なので、背景生成には入れない
                continue
            lines.append(raw)
        out = "\n".join(lines).strip()
        # Bench-match defaults: photoreal background (no people) + avoid obvious top/bottom bands.
        out = out.replace(
            "上品で落ち着いた実写風デジタルアート（または軽いイラスト風）",
            "上品で落ち着いた超リアルな写真風の背景（人物なし、シネマティック、シャープ）",
        )
        out = out.replace(
            "人物の顔が主役。人物は中央〜やや左寄りに大きく（胸上〜上半身）配置。人物の背後に薄い円形スポットライト/リムライトを控えめに。",
            "人物は入れない（顔・身体・人影・シルエットを描かない）。中央に柔らかい円形スポットライト/リムライトを控えめに置き、後で人物を合成できる余白を確保。",
        )
        out = out.replace(
            "上部18%と下部32%は文字合成のため暗い滑らかなグラデーション帯にして情報量を落とす（上部/下部に高コントラストの模様や明るい物体を置かない）",
            "上部と下部は文字合成のため情報量を落とし、自然な暗めの余白/ビネットで読めるスペースを作る（帯のような均一な黒い矩形やはっきりした水平帯は作らない）",
        )
        # Strong constraints: CH26は「本人肖像（別レイヤ）」が必須。背景に人物が出ると事故なので二重に禁止。
        out = (
            out
            + "\n\n"
            + "絶対禁止: 人物/顔/肖像/人影/シルエット/頭部/手/身体/動物を描かない。"
            + "\n"
            + "ABSOLUTE RESTRICTIONS: NO people, NO face, NO portrait, NO silhouette, NO human figure, NO animals."
        ).strip()
        return out
    return p


def _negative_prompt_for_generation(*, channel: str) -> Optional[str]:
    ch = _normalize_channel(channel)
    if ch == "CH26":
        return (
            "text, letters, words, watermark, logo, signature, UI, captions, subtitles, "
            "people, person, human, face, portrait, silhouette, body, hands, head, animals, "
            "文字, 英字, 日本語, ロゴ, 透かし, 署名, UI, 人物, 顔, 肖像, 人影, シルエット"
        )
    return None


def _load_ch26_portrait_policy() -> Dict[str, Any]:
    path = fpaths.thumbnails_root() / "compiler" / "policies" / "ch26_portrait_overrides_v1.yaml"
    if not path.exists():
        return {}
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return doc if isinstance(doc, dict) else {}


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _as_norm_box(value: Any, default: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return default
    out: List[float] = []
    for v in value:
        try:
            f = float(v)
        except Exception:
            return default
        if f < 0.0 or f > 1.0:
            return default
        out.append(f)
    return (out[0], out[1], out[2], out[3])


def _as_norm_offset(value: Any, default: tuple[float, float]) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return default
    try:
        x = float(value[0])
        y = float(value[1])
    except Exception:
        return default
    return (x, y)


def _text_line_spec_path(channel: str, video: str, *, stable: Optional[str] = None) -> Path:
    base_dir = fpaths.thumbnail_assets_dir(channel, video)
    stable_raw = str(stable or "").strip()
    if stable_raw:
        stable_name = Path(stable_raw).name
        if stable_name.lower().endswith(".png"):
            stable_name = stable_name[: -len(".png")]
        stable_name = stable_name.strip()
        if stable_name:
            return base_dir / f"text_line_spec.{stable_name}.json"
    return base_dir / "text_line_spec.json"


def _load_text_line_spec_lines(channel: str, video: str, *, stable: Optional[str] = None) -> Dict[str, Dict[str, float]]:
    stable_raw = str(stable or "").strip()
    stable_name = ""
    if stable_raw:
        stable_name = Path(stable_raw).name
        if stable_name.lower().endswith(".png"):
            stable_name = stable_name[: -len(".png")]
        stable_name = stable_name.strip()

    candidates: List[Path] = []
    if stable_name:
        candidates.append(_text_line_spec_path(channel, video, stable=stable_name))
        # Stable variants must not inherit text_line_spec.json implicitly.
        # Only the primary stable (00_thumb_1) may fall back to legacy text_line_spec.json.
        if stable_name == "00_thumb_1":
            candidates.append(_text_line_spec_path(channel, video, stable=None))
    else:
        candidates.append(_text_line_spec_path(channel, video, stable=None))

    for path in candidates:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        lines_payload = payload.get("lines") if isinstance(payload, dict) else None
        if not isinstance(lines_payload, dict):
            return {}
        out: Dict[str, Dict[str, float]] = {}
        for raw_slot, raw_line in lines_payload.items():
            if not isinstance(raw_slot, str) or not raw_slot.strip():
                continue
            if not isinstance(raw_line, dict):
                continue
            try:
                ox = float(raw_line.get("offset_x", 0.0))
                oy = float(raw_line.get("offset_y", 0.0))
                sc = float(raw_line.get("scale", 1.0))
                rot = float(raw_line.get("rotate_deg", 0.0))
            except Exception:
                continue
            sc = max(0.25, min(4.0, sc))
            rot = max(-180.0, min(180.0, rot))
            out[raw_slot.strip()] = {"offset_x": ox, "offset_y": oy, "scale": sc, "rotate_deg": rot}
        return out
    return {}


def _elements_spec_path(channel: str, video: str, *, stable: Optional[str] = None) -> Path:
    base_dir = fpaths.thumbnail_assets_dir(channel, video)
    stable_raw = str(stable or "").strip()
    if stable_raw:
        stable_name = Path(stable_raw).name
        if stable_name.lower().endswith(".png"):
            stable_name = stable_name[: -len(".png")]
        stable_name = stable_name.strip()
        if stable_name:
            return base_dir / f"elements_spec.{stable_name}.json"
    return base_dir / "elements_spec.json"


def _load_elements_spec_elements(channel: str, video: str, *, stable: Optional[str] = None) -> List[Dict[str, Any]]:
    stable_raw = str(stable or "").strip()
    stable_name = ""
    if stable_raw:
        stable_name = Path(stable_raw).name
        if stable_name.lower().endswith(".png"):
            stable_name = stable_name[: -len(".png")]
        stable_name = stable_name.strip()

    candidates: List[Path] = []
    if stable_name:
        candidates.append(_elements_spec_path(channel, video, stable=stable_name))
        # Stable variants must not inherit elements_spec.json implicitly.
        # Only the primary stable (00_thumb_1) may fall back to legacy elements_spec.json.
        if stable_name == "00_thumb_1":
            candidates.append(_elements_spec_path(channel, video, stable=None))
    else:
        candidates.append(_elements_spec_path(channel, video, stable=None))

    for path in candidates:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        elements_payload = payload.get("elements") if isinstance(payload, dict) else None
        if not isinstance(elements_payload, list):
            return []
        out: List[Dict[str, Any]] = []
        for raw in elements_payload:
            if not isinstance(raw, dict):
                continue
            element_id = str(raw.get("id") or "").strip()
            kind = str(raw.get("kind") or "").strip()
            if not element_id or kind not in {"rect", "circle", "image"}:
                continue
            layer = str(raw.get("layer") or "above_portrait").strip() or "above_portrait"
            if layer not in {"above_portrait", "below_portrait"}:
                layer = "above_portrait"
            try:
                x = float(raw.get("x", 0.5))
                y = float(raw.get("y", 0.5))
                w = float(raw.get("w", 0.2))
                h = float(raw.get("h", 0.2))
            except Exception:
                continue
            try:
                z = int(raw.get("z", 0))
            except Exception:
                z = 0
            try:
                rot = float(raw.get("rotation_deg", 0.0))
            except Exception:
                rot = 0.0
            try:
                opacity = float(raw.get("opacity", 1.0))
            except Exception:
                opacity = 1.0
            fill = str(raw.get("fill") or "").strip() or None
            src_path = str(raw.get("src_path") or "").strip() or None

            stroke_payload = raw.get("stroke") if isinstance(raw.get("stroke"), dict) else None
            stroke: Optional[Dict[str, Any]] = None
            if isinstance(stroke_payload, dict):
                stroke_color = str(stroke_payload.get("color") or "").strip() or None
                try:
                    stroke_width = float(stroke_payload.get("width_px", 0.0))
                except Exception:
                    stroke_width = 0.0
                if stroke_color or abs(float(stroke_width)) > 1e-9:
                    stroke = {"color": stroke_color, "width_px": float(stroke_width)}

            out.append(
                {
                    "id": element_id,
                    "kind": kind,
                    "layer": layer,
                    "z": int(z),
                    "x": float(x),
                    "y": float(y),
                    "w": float(w),
                    "h": float(h),
                    "rotation_deg": float(rot),
                    "opacity": float(opacity),
                    "fill": fill,
                    "stroke": stroke,
                    "src_path": src_path,
                }
            )
        return out
    return []


def _hex_to_rgba(value: str, *, alpha: float) -> tuple[int, int, int, int]:
    s = str(value or "").strip()
    if s.startswith("#") and len(s) == 7:
        try:
            r = int(s[1:3], 16)
            g = int(s[3:5], 16)
            b = int(s[5:7], 16)
        except Exception:
            r, g, b = (255, 255, 255)
    else:
        r, g, b = (255, 255, 255)
    a = int(round(255 * max(0.0, min(1.0, float(alpha)))))
    return (r, g, b, a)


def _resolve_element_src_file(channel: str, video: str, src_path: str) -> Optional[Path]:
    raw = str(src_path or "").strip().lstrip("/").rstrip("/")
    if not raw:
        return None
    normalized = raw.replace("\\", "/")
    rel = Path(normalized)
    if rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
        return None

    ch = _normalize_channel(channel)
    video_dir = fpaths.thumbnail_assets_dir(ch, video)
    channel_root = fpaths.thumbnails_root() / "assets" / ch
    assets_root = fpaths.thumbnails_root() / "assets"

    if normalized.lower().startswith("library/"):
        candidate = (channel_root / rel).resolve()
        try:
            candidate.relative_to(channel_root.resolve())
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    parts = list(rel.parts)
    if parts and parts[0].upper().startswith("CH") and parts[0][2:].isdigit():
        candidate = (assets_root / rel).resolve()
        try:
            candidate.relative_to(assets_root.resolve())
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    candidate = (video_dir / rel).resolve()
    try:
        candidate.relative_to(video_dir.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _apply_elements_to_image(
    base: Image.Image,
    *,
    channel: str,
    video: str,
    elements: List[Dict[str, Any]],
) -> Image.Image:
    out = base.convert("RGBA")
    width, height = out.size
    for el in elements:
        kind = str(el.get("kind") or "").strip()
        if kind not in {"rect", "circle", "image"}:
            continue
        try:
            x = float(el.get("x", 0.5))
            y = float(el.get("y", 0.5))
            w = float(el.get("w", 0.2))
            h = float(el.get("h", 0.2))
        except Exception:
            continue
        try:
            rot = float(el.get("rotation_deg", 0.0))
        except Exception:
            rot = 0.0
        try:
            opacity = float(el.get("opacity", 1.0))
        except Exception:
            opacity = 1.0
        opacity = max(0.0, min(1.0, opacity))

        cx = float(width) * x
        cy = float(height) * y
        w_px = max(1, int(round(float(width) * max(0.01, w))))
        h_px = max(1, int(round(float(height) * max(0.01, h))))
        left = int(round(cx - w_px / 2))
        top = int(round(cy - h_px / 2))

        layer_img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        if kind == "image":
            src_file = _resolve_element_src_file(channel, video, str(el.get("src_path") or ""))
            if src_file is None:
                continue
            try:
                with Image.open(src_file) as src_in:
                    src = src_in.convert("RGBA")
            except Exception:
                continue
            fitted = ImageOps.fit(src, (w_px, h_px), method=Image.LANCZOS, centering=(0.5, 0.5))
            if opacity < 1.0:
                r, g, b, a = fitted.split()
                a = a.point(lambda p: int(round(p * opacity)))
                fitted = Image.merge("RGBA", (r, g, b, a))
            layer_img.paste(fitted, (left, top), fitted)
        else:
            fill = str(el.get("fill") or "").strip() or "#ffffff"
            draw = ImageDraw.Draw(layer_img)
            bbox = [left, top, left + w_px, top + h_px]
            fill_rgba = _hex_to_rgba(fill, alpha=opacity)
            stroke = el.get("stroke") if isinstance(el.get("stroke"), dict) else None
            outline = None
            stroke_width = 0
            if isinstance(stroke, dict):
                stroke_color = str(stroke.get("color") or "").strip() or "#000000"
                try:
                    stroke_width = int(round(float(stroke.get("width_px", 0.0))))
                except Exception:
                    stroke_width = 0
                if stroke_width > 0:
                    outline = _hex_to_rgba(stroke_color, alpha=opacity)
            if kind == "rect":
                draw.rectangle(bbox, fill=fill_rgba, outline=outline, width=max(0, stroke_width))
            else:
                draw.ellipse(bbox, fill=fill_rgba, outline=outline, width=max(0, stroke_width))

        rot = max(-180.0, min(180.0, rot))
        if abs(rot) > 1e-6:
            layer_img = layer_img.rotate(-float(rot), resample=Image.BICUBIC, center=(cx, cy), expand=False)

        out = Image.alpha_composite(out, layer_img)
    return out


def _apply_elements_to_path(
    base_image_path: Path,
    *,
    channel: str,
    video: str,
    elements: List[Dict[str, Any]],
    out_path: Path,
) -> Path:
    if not elements:
        return base_image_path
    with Image.open(base_image_path) as base_in:
        base = base_in.convert("RGBA")
    composed = _apply_elements_to_image(base, channel=channel, video=video, elements=elements)
    save_png_atomic(composed, out_path, mode="draft", verify=True)
    return out_path


def _shift_layer_rgba(img: Image.Image, *, dx: int, dy: int) -> Image.Image:
    layer = img.convert("RGBA")
    w, h = layer.size
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(layer, (int(dx), int(dy)), layer)
    return out


def _compose_text_canva_like(
    base_image_path: Path,
    *,
    text_layout_spec: Dict[str, Any],
    video_id: str,
    out_path: Path,
    output_mode: PngOutputMode,
    template_id: str,
    resolved_text_by_slot: Dict[str, str],
    text_line_spec_lines: Dict[str, Dict[str, float]],
    text_offset_x: float = 0.0,
    text_offset_y: float = 0.0,
    template_id_override: Optional[str] = None,
    effects_override: Optional[Dict[str, Any]] = None,
    overlays_override: Optional[Dict[str, Any]] = None,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(base_image_path) as base_in:
        base = base_in.convert("RGBA")
    width, height = base.size

    templates = text_layout_spec.get("templates") if isinstance(text_layout_spec, dict) else None
    tpl = templates.get(template_id) if isinstance(templates, dict) else None
    slots_payload = tpl.get("slots") if isinstance(tpl, dict) else None
    if not isinstance(slots_payload, dict):
        save_png_atomic(base, out_path, mode=output_mode, verify=True)
        return

    slot_keys = [str(k).strip() for k in slots_payload.keys() if isinstance(k, str) and str(k).strip()]
    blank_all = {k: "" for k in slot_keys}
    slot_boxes: Dict[str, List[float]] = {}
    for slot_key in slot_keys:
        cfg = slots_payload.get(slot_key)
        if not isinstance(cfg, dict):
            continue
        box = cfg.get("box")
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        try:
            nums = [float(box[0]), float(box[1]), float(box[2]), float(box[3])]
        except Exception:
            continue
        slot_boxes[slot_key] = nums

    overlays_disabled = {
        "left_tsz": {"enabled": False},
        "top_band": {"enabled": False},
        "bottom_band": {"enabled": False},
    }

    with tempfile.TemporaryDirectory(prefix=f"thumb_text_{video_id.replace('-', '_')}_") as tmpdir:
        tmp_root = Path(tmpdir)
        transparent_base = tmp_root / "base_transparent.png"
        Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(transparent_base, format="PNG")

        overlays_img = compose_text_to_png(
            transparent_base,
            text_layout_spec=text_layout_spec,
            video_id=video_id,
            out_path=tmp_root / "overlays.png",
            output_mode="draft",
            text_override=blank_all,
            template_id_override=template_id_override,
            effects_override=effects_override,
            overlays_override=overlays_override,
        ).convert("RGBA")
        base = Image.alpha_composite(base, overlays_img)

        for slot_key in slot_keys:
            text_value = str(resolved_text_by_slot.get(slot_key) or "").strip()
            if not text_value:
                continue

            slot_override = dict(blank_all)
            slot_override[slot_key] = text_value
            slot_img = compose_text_to_png(
                transparent_base,
                text_layout_spec=text_layout_spec,
                video_id=video_id,
                out_path=tmp_root / f"text__{slot_key}.png",
                output_mode="draft",
                text_override=slot_override,
                template_id_override=template_id_override,
                effects_override=effects_override,
                overlays_override=overlays_disabled,
            ).convert("RGBA")

            line = text_line_spec_lines.get(slot_key) if isinstance(text_line_spec_lines, dict) else None
            try:
                ox = float(line.get("offset_x", 0.0)) if isinstance(line, dict) else 0.0
                oy = float(line.get("offset_y", 0.0)) if isinstance(line, dict) else 0.0
            except Exception:
                ox, oy = (0.0, 0.0)
            try:
                rot = float(line.get("rotate_deg", 0.0)) if isinstance(line, dict) else 0.0
            except Exception:
                rot = 0.0
            rot = max(-180.0, min(180.0, rot))

            if abs(rot) > 1e-6:
                box = slot_boxes.get(slot_key)
                if isinstance(box, list) and len(box) == 4:
                    cx = (float(box[0]) + float(box[2]) * 0.5) * float(width)
                    cy = (float(box[1]) + float(box[3]) * 0.5) * float(height)
                else:
                    cx = float(width) * 0.5
                    cy = float(height) * 0.5
                # Pillow rotates CCW; match CSS rotate() (clockwise) by flipping the sign.
                slot_img = slot_img.rotate(-float(rot), resample=Image.BICUBIC, center=(cx, cy), expand=False)

            dx = int(round(float(width) * float(text_offset_x + ox)))
            dy = int(round(float(height) * float(text_offset_y + oy)))
            if dx or dy:
                slot_img = _shift_layer_rgba(slot_img, dx=dx, dy=dy)
            base = Image.alpha_composite(base, slot_img)

    save_png_atomic(base, out_path, mode=output_mode, verify=True)


def build_channel_thumbnails(
    *,
    channel: str,
    targets: List[BuildTarget],
    width: int,
    height: int,
    stable_thumb_name: str = "00_thumb.png",
    variant_label: Optional[str] = None,
    force: bool,
    skip_generate: bool,
    continue_on_error: bool,
    max_gen_attempts: int,
    export_flat: bool,
    flat_name_suffix: str,
    sleep_sec: float,
    bg_brightness: float,
    bg_contrast: float,
    bg_color: float,
    bg_gamma: float,
    bg_zoom: float = 1.0,
    bg_pan_x: float = 0.0,
    bg_pan_y: float = 0.0,
    bg_band_brightness: float = 1.0,
    bg_band_contrast: float = 1.0,
    bg_band_color: float = 1.0,
    bg_band_gamma: float = 1.0,
    bg_band_x0: float = 0.0,
    bg_band_x1: float = 0.0,
    bg_band_power: float = 1.0,
    regen_bg: bool = False,
    build_id: Optional[str] = None,
    output_mode: PngOutputMode = "final",
) -> None:
    if bool(regen_bg) and bool(skip_generate):
        raise ValueError("regen_bg cannot be used with skip_generate")
    stable_thumb_name = str(stable_thumb_name or "").strip() or "00_thumb.png"
    if Path(stable_thumb_name).name != stable_thumb_name:
        raise ValueError("stable_thumb_name must be a filename (no directories)")
    if Path(stable_thumb_name).suffix.lower() != ".png":
        raise ValueError("stable_thumb_name must end with .png")
    stable_id: Optional[str] = None
    if stable_thumb_name != "00_thumb.png":
        stable_id = Path(stable_thumb_name).stem
    ch = _normalize_channel(channel)
    build_id = str(build_id or "").strip() or datetime.now(timezone.utc).strftime("build_%Y%m%dT%H%M%SZ")
    resolved_variant_label = str(variant_label or "").strip()
    if not resolved_variant_label:
        resolved_variant_label = "thumb_00" if stable_thumb_name == "00_thumb.png" else Path(stable_thumb_name).stem
    compiler_defaults = _load_compiler_defaults_from_templates(ch)
    img_id, txt_id = resolve_channel_layer_spec_ids(ch)
    if not txt_id:
        txt_id = "text_layout_v3"
    text_spec = load_layer_spec_yaml(txt_id)
    text_spec_typed = load_text_layout_v3_typed(txt_id)

    # image_prompts / model_key are only required when generating backgrounds.
    image_spec: Optional[ImagePromptsSpecV3] = None
    model_key = _resolve_model_key_from_templates(ch)
    needs_generation = (not bool(skip_generate)) or bool(regen_bg)
    if needs_generation:
        if not img_id:
            img_id = "image_prompts_v3"
        image_spec = load_image_prompts_v3_typed(img_id)
        if not model_key:
            raise RuntimeError(f"image_model_key not found in workspaces/thumbnails/templates.json for channel={ch}")

    assets_root = fpaths.thumbnails_root() / "assets" / ch
    assets_root.mkdir(parents=True, exist_ok=True)

    client = ImageClient() if needs_generation else None
    portrait_policy = _load_ch26_portrait_policy() if ch == "CH26" else {}

    bg_defaults = compiler_defaults.get("bg_enhance") if isinstance(compiler_defaults.get("bg_enhance"), dict) else {}
    pan_defaults = compiler_defaults.get("bg_pan_zoom") if isinstance(compiler_defaults.get("bg_pan_zoom"), dict) else {}
    band_defaults = compiler_defaults.get("bg_enhance_band") if isinstance(compiler_defaults.get("bg_enhance_band"), dict) else {}

    def _default_float(cur: float, defaults: Dict[str, Any], key: str, *, identity: float) -> float:
        if abs(float(cur) - float(identity)) < 1e-9 and isinstance(defaults.get(key), (int, float)):
            return float(defaults[key])
        return float(cur)

    base_bg_brightness = _default_float(float(bg_brightness), bg_defaults, "brightness", identity=1.0)
    base_bg_contrast = _default_float(float(bg_contrast), bg_defaults, "contrast", identity=1.0)
    base_bg_color = _default_float(float(bg_color), bg_defaults, "color", identity=1.0)
    base_bg_gamma = _default_float(float(bg_gamma), bg_defaults, "gamma", identity=1.0)

    base_bg_zoom = _default_float(float(bg_zoom), pan_defaults, "zoom", identity=1.0)
    base_bg_pan_x = _default_float(float(bg_pan_x), pan_defaults, "pan_x", identity=0.0)
    base_bg_pan_y = _default_float(float(bg_pan_y), pan_defaults, "pan_y", identity=0.0)

    base_band_x0 = _default_float(float(bg_band_x0), band_defaults, "x0", identity=0.0)
    base_band_x1 = _default_float(float(bg_band_x1), band_defaults, "x1", identity=0.0)
    base_band_power = _default_float(float(bg_band_power), band_defaults, "power", identity=1.0)
    base_band_brightness = _default_float(float(bg_band_brightness), band_defaults, "brightness", identity=1.0)
    base_band_contrast = _default_float(float(bg_band_contrast), band_defaults, "contrast", identity=1.0)
    base_band_color = _default_float(float(bg_band_color), band_defaults, "color", identity=1.0)
    base_band_gamma = _default_float(float(bg_band_gamma), band_defaults, "gamma", identity=1.0)

    for idx, target in enumerate(targets, start=1):
        video_dir = assets_root / target.video
        video_dir.mkdir(parents=True, exist_ok=True)

        out_bg = video_dir / "10_bg.png"
        stable_thumb = video_dir / stable_thumb_name
        flat_out: Optional[Path] = None
        if export_flat:
            suffix = str(flat_name_suffix or "").strip()
            if suffix and not suffix.startswith("_"):
                suffix = "_" + suffix
            flat_out = assets_root / f"{target.video}{suffix}.png"

        if stable_thumb.exists() and not force:
            if flat_out and not flat_out.exists():
                flat_out.write_bytes(stable_thumb.read_bytes())
                print(f"[{idx}/{len(targets)}] {target.video_id}: export-flat -> {flat_out.name}")
            else:
                print(f"[{idx}/{len(targets)}] {target.video_id}: skip (already built)")
            continue

        build_dir = video_dir / "compiler" / build_id
        build_dir.mkdir(parents=True, exist_ok=True)
        build_thumb = build_dir / "out_01.png"
        build_meta_path = build_dir / "build_meta.json"

        thumb_spec = load_thumb_spec(ch, target.video, stable=stable_id)
        overrides_leaf = extract_normalized_override_leaf(thumb_spec.payload) if thumb_spec else {}

        video_bg_brightness = float(overrides_leaf.get("overrides.bg_enhance.brightness", base_bg_brightness))
        video_bg_contrast = float(overrides_leaf.get("overrides.bg_enhance.contrast", base_bg_contrast))
        video_bg_color = float(overrides_leaf.get("overrides.bg_enhance.color", base_bg_color))
        video_bg_gamma = float(overrides_leaf.get("overrides.bg_enhance.gamma", base_bg_gamma))

        video_bg_zoom = float(overrides_leaf.get("overrides.bg_pan_zoom.zoom", base_bg_zoom))
        video_bg_pan_x = float(overrides_leaf.get("overrides.bg_pan_zoom.pan_x", base_bg_pan_x))
        video_bg_pan_y = float(overrides_leaf.get("overrides.bg_pan_zoom.pan_y", base_bg_pan_y))

        video_band_x0 = float(overrides_leaf.get("overrides.bg_enhance_band.x0", base_band_x0))
        video_band_x1 = float(overrides_leaf.get("overrides.bg_enhance_band.x1", base_band_x1))
        video_band_power = float(overrides_leaf.get("overrides.bg_enhance_band.power", base_band_power))
        video_band_brightness = float(overrides_leaf.get("overrides.bg_enhance_band.brightness", base_band_brightness))
        video_band_contrast = float(overrides_leaf.get("overrides.bg_enhance_band.contrast", base_band_contrast))
        video_band_color = float(overrides_leaf.get("overrides.bg_enhance_band.color", base_band_color))
        video_band_gamma = float(overrides_leaf.get("overrides.bg_enhance_band.gamma", base_band_gamma))

        video_text_scale = float(overrides_leaf.get("overrides.text_scale", 1.0))
        video_text_offset_x = float(overrides_leaf.get("overrides.text_offset_x", 0.0))
        video_text_offset_y = float(overrides_leaf.get("overrides.text_offset_y", 0.0))

        template_id_override = str(overrides_leaf.get("overrides.text_template_id") or "").strip() or None

        effects_override: Optional[Dict[str, Any]] = None
        overlays_override: Optional[Dict[str, Any]] = None
        if overrides_leaf:
            stroke: Dict[str, Any] = {}
            shadow: Dict[str, Any] = {}
            glow: Dict[str, Any] = {}
            fills: Dict[str, Any] = {}
            if "overrides.text_effects.stroke.width_px" in overrides_leaf:
                stroke["width_px"] = overrides_leaf["overrides.text_effects.stroke.width_px"]
            if "overrides.text_effects.stroke.color" in overrides_leaf:
                stroke["color"] = overrides_leaf["overrides.text_effects.stroke.color"]
            if "overrides.text_effects.shadow.alpha" in overrides_leaf:
                shadow["alpha"] = overrides_leaf["overrides.text_effects.shadow.alpha"]
            if "overrides.text_effects.shadow.offset_px" in overrides_leaf:
                off = overrides_leaf["overrides.text_effects.shadow.offset_px"]
                if isinstance(off, tuple) and len(off) == 2:
                    shadow["offset_px"] = [int(off[0]), int(off[1])]
                else:
                    shadow["offset_px"] = off
            if "overrides.text_effects.shadow.blur_px" in overrides_leaf:
                shadow["blur_px"] = overrides_leaf["overrides.text_effects.shadow.blur_px"]
            if "overrides.text_effects.shadow.color" in overrides_leaf:
                shadow["color"] = overrides_leaf["overrides.text_effects.shadow.color"]
            if "overrides.text_effects.glow.alpha" in overrides_leaf:
                glow["alpha"] = overrides_leaf["overrides.text_effects.glow.alpha"]
            if "overrides.text_effects.glow.blur_px" in overrides_leaf:
                glow["blur_px"] = overrides_leaf["overrides.text_effects.glow.blur_px"]
            if "overrides.text_effects.glow.color" in overrides_leaf:
                glow["color"] = overrides_leaf["overrides.text_effects.glow.color"]

            for fill_key in ("white_fill", "red_fill", "yellow_fill", "hot_red_fill", "purple_fill"):
                p = f"overrides.text_fills.{fill_key}.color"
                if p in overrides_leaf:
                    fills[fill_key] = {"color": overrides_leaf[p]}
            eff = {}
            if stroke:
                eff["stroke"] = stroke
            if shadow:
                eff["shadow"] = shadow
            if glow:
                eff["glow"] = glow
            if fills:
                eff.update(fills)
            if eff:
                effects_override = eff

            left_tsz: Dict[str, Any] = {}
            top_band: Dict[str, Any] = {}
            bottom_band: Dict[str, Any] = {}
            for k in ("enabled", "color", "alpha_left", "alpha_right", "x0", "x1"):
                p = f"overrides.overlays.left_tsz.{k}"
                if p in overrides_leaf:
                    left_tsz[k] = overrides_leaf[p]
            for k in ("enabled", "color", "alpha_top", "alpha_bottom", "y0", "y1"):
                p = f"overrides.overlays.top_band.{k}"
                if p in overrides_leaf:
                    top_band[k] = overrides_leaf[p]
            for k in ("enabled", "color", "alpha_top", "alpha_bottom", "y0", "y1"):
                p = f"overrides.overlays.bottom_band.{k}"
                if p in overrides_leaf:
                    bottom_band[k] = overrides_leaf[p]
            ov = {}
            if left_tsz:
                ov["left_tsz"] = left_tsz
            if top_band:
                ov["top_band"] = top_band
            if bottom_band:
                ov["bottom_band"] = bottom_band
            if ov:
                overlays_override = ov

        copy_override: Dict[str, str] = {}
        for k in ("upper", "title", "lower"):
            p = f"overrides.copy_override.{k}"
            if p in overrides_leaf and isinstance(overrides_leaf.get(p), str):
                copy_override[k] = str(overrides_leaf[p]).strip()

        bg_source = resolve_background_source(video_dir=video_dir, channel_root=assets_root, video=target.video)
        bg_src = None if bool(regen_bg) else bg_source.bg_src
        legacy_moved_from = None if bool(regen_bg) else bg_source.legacy_moved_from

        generated: Optional[Dict[str, Any]] = None
        if bg_src is None:
            if skip_generate:
                print(f"[{idx}/{len(targets)}] {target.video_id}: missing bg (skip_generate)")
                continue
            prompt = None
            if image_spec is not None:
                prompt = next((it.prompt_ja for it in image_spec.items if it.video_id == target.video_id), None)
            if not isinstance(prompt, str) or not prompt.strip():
                prompt = _load_planning_image_prompt(ch, target.video)
            if not isinstance(prompt, str) or not prompt.strip():
                raise RuntimeError(
                    f"image prompt missing for {target.video_id} "
                    f"(layer_specs image_prompts item or Planning CSV image prompt column is required)"
                )
            prompt = _sanitize_prompt_for_generation(channel=ch, prompt=prompt)
            negative_prompt = _negative_prompt_for_generation(channel=ch)
            try:
                gen = generate_background_with_retries(
                    client=client,
                    prompt=prompt,
                    model_key=model_key,
                    negative_prompt=negative_prompt,
                    out_raw_path=video_dir / "90_bg_ai_raw.png",
                    video_id=target.video_id,
                    max_attempts=int(max_gen_attempts),
                    sleep_sec=float(sleep_sec),
                )
            except Exception as exc:  # noqa: BLE001
                msg = f"[{idx}/{len(targets)}] {target.video_id}: generation failed ({exc})"
                if continue_on_error:
                    print(msg)
                    continue
                raise
            bg_src = gen.raw_path
            generated = gen.generated

        if not bg_src:
            raise RuntimeError(f"background source resolution failed for {target.video_id}")
        crop_resize_to_16x9(bg_src, out_bg, width=width, height=height, output_mode=output_mode)

        print(f"[{idx}/{len(targets)}] {target.video_id}: composing text ...")
        bg_params = BgEnhanceParams(
            brightness=float(video_bg_brightness),
            contrast=float(video_bg_contrast),
            color=float(video_bg_color),
            gamma=float(video_bg_gamma),
        )
        band_params = BgEnhanceParams(
            brightness=float(video_band_brightness),
            contrast=float(video_band_contrast),
            color=float(video_band_color),
            gamma=float(video_band_gamma),
        )
        item = find_text_layout_item_for_video(text_spec, target.video_id) if isinstance(text_spec, dict) else None
        template_id = str(item.get("template_id") or "").strip() if isinstance(item, dict) else ""
        if template_id_override:
            template_id = str(template_id_override).strip() or template_id
        if not template_id:
            template_id = _default_text_template_id(text_spec)
        templates = text_spec.get("templates") if isinstance(text_spec, dict) else None
        slots = None
        if template_id and isinstance(templates, dict):
            tpl = templates.get(template_id)
            slots = tpl.get("slots") if isinstance(tpl, dict) else None
        text_payload = item.get("text") if isinstance(item, dict) else None
        planning_copy = _load_planning_copy(ch, target.video)
        if copy_override:
            for k, v in copy_override.items():
                if v:
                    planning_copy[k] = v

        def _override_for_slot(slot_name: str) -> str:
            name = str(slot_name or "").strip().lower()
            if name in {"line1", "upper", "top"}:
                return str(copy_override.get("upper") or "").strip()
            if name in {"line2", "title", "main"}:
                return str(copy_override.get("title") or "").strip()
            if name in {"line3", "lower", "accent"}:
                return str(copy_override.get("lower") or "").strip()
            return ""

        text_override: Dict[str, str] = {}
        if isinstance(slots, dict):
            for slot_name in slots.keys():
                slot_key = str(slot_name or "").strip()
                if not slot_key:
                    continue
                forced = _override_for_slot(slot_key)
                if forced:
                    text_override[slot_key] = forced
                    continue
                authored = str(text_payload.get(slot_key) or "").strip() if isinstance(text_payload, dict) else ""
                if authored:
                    continue
                val = _planning_value_for_slot(slot_key, planning_copy)
                if val:
                    text_override[slot_key] = val

        text_line_spec_lines = _load_text_line_spec_lines(ch, target.video, stable=stable_id)

        text_spec_for_render = text_spec
        if not isinstance(item, dict) and template_id:
            text_spec_for_render = _ensure_text_layout_item(
                text_layout_spec=text_spec_for_render,
                video_id=target.video_id,
                template_id=template_id,
                title=str(target.video_id),
            )
        needs_text_mutation = bool(text_line_spec_lines) or abs(float(video_text_scale) - 1.0) > 1e-6 or (
            abs(float(video_text_offset_x)) > 1e-9 or abs(float(video_text_offset_y)) > 1e-9
        )
        if needs_text_mutation and isinstance(text_spec, dict):
            text_spec_for_render = copy.deepcopy(text_spec)
            templates_out = text_spec_for_render.get("templates") if isinstance(text_spec_for_render, dict) else None
            tpl_out = templates_out.get(template_id) if isinstance(templates_out, dict) and template_id else None
            slots_out = tpl_out.get("slots") if isinstance(tpl_out, dict) else None
            if isinstance(slots_out, dict):
                for slot_key, slot_cfg in slots_out.items():
                    if not isinstance(slot_cfg, dict):
                        continue
                    if abs(float(video_text_scale) - 1.0) > 1e-6:
                        base_size = slot_cfg.get("base_size_px")
                        if isinstance(base_size, (int, float)):
                            scaled = int(round(float(base_size) * float(video_text_scale)))
                            slot_cfg["base_size_px"] = max(1, scaled)
                    line = text_line_spec_lines.get(str(slot_key).strip()) if isinstance(slot_key, str) else None
                    if isinstance(line, dict):
                        line_scale = line.get("scale", 1.0)
                        try:
                            line_scale_f = float(line_scale)
                        except Exception:
                            line_scale_f = 1.0
                        if abs(float(line_scale_f) - 1.0) > 1e-6:
                            base_size = slot_cfg.get("base_size_px")
                            if isinstance(base_size, (int, float)):
                                scaled = int(round(float(base_size) * float(line_scale_f)))
                                slot_cfg["base_size_px"] = max(1, scaled)
        if not isinstance(item, dict) and template_id:
            text_spec_for_render = _ensure_text_layout_item(
                text_layout_spec=text_spec_for_render,
                video_id=target.video_id,
                template_id=template_id,
                title=str(target.video_id),
            )

        resolved_text_by_slot: Dict[str, str] = {}
        if isinstance(slots, dict):
            for slot_name in slots.keys():
                slot_key = str(slot_name or "").strip()
                if not slot_key:
                    continue
                forced = _override_for_slot(slot_key)
                authored = str(text_payload.get(slot_key) or "").strip() if isinstance(text_payload, dict) else ""
                planned = _planning_value_for_slot(slot_key, planning_copy)
                resolved_text_by_slot[slot_key] = forced or authored or planned or ""

        use_canva_text = abs(float(video_text_offset_x)) > 1e-9 or abs(float(video_text_offset_y)) > 1e-9
        if not use_canva_text:
            for line in (text_line_spec_lines or {}).values():
                if not isinstance(line, dict):
                    continue
                try:
                    ox = float(line.get("offset_x", 0.0))
                    oy = float(line.get("offset_y", 0.0))
                    rot = float(line.get("rotate_deg", 0.0))
                except Exception:
                    continue
                if abs(float(ox)) > 1e-9 or abs(float(oy)) > 1e-9 or abs(float(rot)) > 1e-6:
                    use_canva_text = True
                    break

        elements_spec = _load_elements_spec_elements(ch, target.video, stable=stable_id)
        elements_below = sorted(
            [el for el in elements_spec if str(el.get("layer") or "above_portrait") == "below_portrait"],
            key=lambda el: int(el.get("z", 0)) if isinstance(el.get("z"), (int, float, str)) else 0,
        )
        elements_above = sorted(
            [el for el in elements_spec if str(el.get("layer") or "above_portrait") != "below_portrait"],
            key=lambda el: int(el.get("z", 0)) if isinstance(el.get("z"), (int, float, str)) else 0,
        )

        with enhanced_bg_path(
            out_bg,
            params=bg_params,
            zoom=float(video_bg_zoom),
            pan_x=float(video_bg_pan_x),
            pan_y=float(video_bg_pan_y),
            band_params=band_params,
            band_x0=float(video_band_x0),
            band_x1=float(video_band_x1),
            band_power=float(video_band_power),
            temp_prefix=f"{target.video_id}_bg_",
        ) as base_for_text:
            portrait_path = find_existing_portrait(video_dir)
            portrait_used = False
            if portrait_path is not None:
                portrait_enabled = bool(overrides_leaf.get("overrides.portrait.enabled", stable_id != "00_thumb_2"))
                if not portrait_enabled:
                    base_for_text_out = base_for_text
                    if elements_below:
                        base_for_text_out = _apply_elements_to_path(
                            base_for_text_out,
                            channel=ch,
                            video=target.video,
                            elements=elements_below,
                            out_path=build_dir / "base__elements_below.png",
                        )
                    if elements_above:
                        base_for_text_out = _apply_elements_to_path(
                            base_for_text_out,
                            channel=ch,
                            video=target.video,
                            elements=elements_above,
                            out_path=build_dir / "base__elements_above.png",
                        )
                    if use_canva_text:
                        _compose_text_canva_like(
                            base_for_text_out,
                            text_layout_spec=text_spec_for_render,
                            video_id=target.video_id,
                            out_path=build_thumb,
                            output_mode=output_mode,
                            template_id=template_id,
                            resolved_text_by_slot=resolved_text_by_slot,
                            text_line_spec_lines=text_line_spec_lines,
                            text_offset_x=float(video_text_offset_x),
                            text_offset_y=float(video_text_offset_y),
                            template_id_override=template_id_override,
                            effects_override=effects_override,
                            overlays_override=overlays_override,
                        )
                    else:
                        compose_text_to_png(
                            base_for_text_out,
                            text_layout_spec=text_spec_for_render,
                            video_id=target.video_id,
                            out_path=build_thumb,
                            output_mode=output_mode,
                            text_override=text_override if text_override else None,
                            template_id_override=template_id_override,
                            effects_override=effects_override,
                            overlays_override=overlays_override,
                        )
                else:
                    # CH26 benchmark: portrait is composited as a separate layer (本人肖像素材を使用)
                    dest_box_norm_default = (0.29, 0.06, 0.42, 0.76)
                    dest_box_norm = dest_box_norm_default
                    anchor = "bottom_center"
                    portrait_zoom = 1.0
                    portrait_offset_px = (0, 0)
                    off_norm = (0.0, 0.0)
                    trim_transparent = False

                    fg_brightness = 1.20
                    fg_contrast = 1.08
                    fg_color = 0.98

                    if ch == "CH26":
                        cfg_defaults = (
                            portrait_policy.get("defaults") if isinstance(portrait_policy.get("defaults"), dict) else {}
                        )
                        cfg_overrides = (
                            portrait_policy.get("overrides") if isinstance(portrait_policy.get("overrides"), dict) else {}
                        )
                        ov = cfg_overrides.get(target.video) if isinstance(cfg_overrides, dict) else None
                        ov = ov if isinstance(ov, dict) else {}

                        dest_box_norm = _as_norm_box(
                            ov.get("dest_box") or cfg_defaults.get("dest_box"), dest_box_norm_default
                        )
                        anchor = str(ov.get("anchor") or cfg_defaults.get("anchor") or anchor).strip() or anchor
                        portrait_zoom = _as_float(ov.get("zoom") if "zoom" in ov else cfg_defaults.get("zoom"), 1.0)
                        off_norm = _as_norm_offset(
                            ov.get("offset") if "offset" in ov else cfg_defaults.get("offset"), (0.0, 0.0)
                        )
                        trim_transparent = bool(
                            ov.get("trim_transparent") if "trim_transparent" in ov else cfg_defaults.get("trim_transparent")
                        )

                        fg_defaults = cfg_defaults.get("fg") if isinstance(cfg_defaults.get("fg"), dict) else {}
                        fg_override = ov.get("fg") if isinstance(ov.get("fg"), dict) else {}
                        fg_brightness = _as_float(
                            fg_override.get("brightness") if "brightness" in fg_override else fg_defaults.get("brightness"),
                            1.26,
                        )
                        fg_contrast = _as_float(
                            fg_override.get("contrast") if "contrast" in fg_override else fg_defaults.get("contrast"),
                            1.10,
                        )
                        fg_color = _as_float(
                            fg_override.get("color") if "color" in fg_override else fg_defaults.get("color"), 1.00
                        )

                    # Per-video thumb_spec overrides should win over channel policy.
                    # These are normalized offsets (relative to canvas width/height).
                    if "overrides.portrait.zoom" in overrides_leaf:
                        portrait_zoom = float(overrides_leaf["overrides.portrait.zoom"])
                    off_x = float(off_norm[0]) if isinstance(off_norm, tuple) and len(off_norm) == 2 else 0.0
                    off_y = float(off_norm[1]) if isinstance(off_norm, tuple) and len(off_norm) == 2 else 0.0
                    if "overrides.portrait.offset_x" in overrides_leaf:
                        off_x = float(overrides_leaf["overrides.portrait.offset_x"])
                    if "overrides.portrait.offset_y" in overrides_leaf:
                        off_y = float(overrides_leaf["overrides.portrait.offset_y"])
                    portrait_offset_px = (int(round(width * off_x)), int(round(height * off_y)))
                    if "overrides.portrait.trim_transparent" in overrides_leaf:
                        trim_transparent = bool(overrides_leaf["overrides.portrait.trim_transparent"])
                    if "overrides.portrait.fg_brightness" in overrides_leaf:
                        fg_brightness = float(overrides_leaf["overrides.portrait.fg_brightness"])
                    if "overrides.portrait.fg_contrast" in overrides_leaf:
                        fg_contrast = float(overrides_leaf["overrides.portrait.fg_contrast"])
                    if "overrides.portrait.fg_color" in overrides_leaf:
                        fg_color = float(overrides_leaf["overrides.portrait.fg_color"])

                    dest_box_px = (
                        int(round(width * dest_box_norm[0])),
                        int(round(height * dest_box_norm[1])),
                        int(round(width * dest_box_norm[2])),
                        int(round(height * dest_box_norm[3])),
                    )
                    suppress_box_px = dest_box_px
                    if portrait_offset_px != (0, 0):
                        shifted_box_px = (
                            int(dest_box_px[0]) + int(portrait_offset_px[0]),
                            int(dest_box_px[1]) + int(portrait_offset_px[1]),
                            int(dest_box_px[2]),
                            int(dest_box_px[3]),
                        )
                        left = min(int(dest_box_px[0]), int(shifted_box_px[0]))
                        top = min(int(dest_box_px[1]), int(shifted_box_px[1]))
                        right = max(
                            int(dest_box_px[0]) + int(dest_box_px[2]),
                            int(shifted_box_px[0]) + int(shifted_box_px[2]),
                        )
                        bottom = max(
                            int(dest_box_px[1]) + int(dest_box_px[3]),
                            int(shifted_box_px[1]) + int(shifted_box_px[3]),
                        )
                        suppress_box_px = (left, top, max(1, right - left), max(1, bottom - top))
                    suppress_bg = bool(overrides_leaf.get("overrides.portrait.suppress_bg", ch == "CH26"))
                    # CH26 backgrounds may already contain a portrait; when portrait is enabled we must suppress it.
                    if ch == "CH26":
                        suppress_bg = True
                    if suppress_bg:
                        # CH26 (and any channel opting into suppress_bg) must not leave a recognizable "ghost face"
                        # behind the overlaid portrait. Use a hard suppression that effectively blacks-out the region.
                        suppress_kwargs = {
                            "pad_ratio": 0.25,
                            "mask_blur_ratio": 0.01,
                            "brightness": 0.0,
                            "contrast": 1.0,
                        }
                        with suppressed_center_region_path(
                            base_for_text,
                            dest_box_px=suppress_box_px,
                            temp_prefix=f"{target.video_id}_bg_supp_",
                            **suppress_kwargs,
                        ) as suppressed_bg:
                            base_for_portrait = suppressed_bg
                            if elements_below:
                                base_for_portrait = _apply_elements_to_path(
                                    base_for_portrait,
                                    channel=ch,
                                    video=target.video,
                                    elements=elements_below,
                                    out_path=build_dir / "base__elements_below.png",
                                )
                            with composited_portrait_path(
                                base_for_portrait,
                                portrait_path=portrait_path,
                                dest_box_px=dest_box_px,
                                temp_prefix=f"{target.video_id}_base_",
                                anchor=anchor,
                                portrait_zoom=float(portrait_zoom),
                                portrait_offset_px=portrait_offset_px,
                                trim_transparent=bool(trim_transparent),
                                fg_brightness=fg_brightness,
                                fg_contrast=fg_contrast,
                                fg_color=fg_color,
                            ) as base_with_portrait:
                                portrait_used = True
                                base_for_text_out = base_with_portrait
                                if elements_above:
                                    base_for_text_out = _apply_elements_to_path(
                                        base_for_text_out,
                                        channel=ch,
                                        video=target.video,
                                        elements=elements_above,
                                        out_path=build_dir / "base__elements_above.png",
                                    )
                                if use_canva_text:
                                    _compose_text_canva_like(
                                        base_for_text_out,
                                        text_layout_spec=text_spec_for_render,
                                        video_id=target.video_id,
                                        out_path=build_thumb,
                                        output_mode=output_mode,
                                        template_id=template_id,
                                        resolved_text_by_slot=resolved_text_by_slot,
                                        text_line_spec_lines=text_line_spec_lines,
                                        text_offset_x=float(video_text_offset_x),
                                        text_offset_y=float(video_text_offset_y),
                                        template_id_override=template_id_override,
                                        effects_override=effects_override,
                                        overlays_override=overlays_override,
                                    )
                                else:
                                    compose_text_to_png(
                                        base_for_text_out,
                                        text_layout_spec=text_spec_for_render,
                                        video_id=target.video_id,
                                        out_path=build_thumb,
                                        output_mode=output_mode,
                                        text_override=text_override if text_override else None,
                                        template_id_override=template_id_override,
                                        effects_override=effects_override,
                                        overlays_override=overlays_override,
                                    )
                    else:
                        base_for_portrait = base_for_text
                        if elements_below:
                            base_for_portrait = _apply_elements_to_path(
                                base_for_portrait,
                                channel=ch,
                                video=target.video,
                                elements=elements_below,
                                out_path=build_dir / "base__elements_below.png",
                            )
                        with composited_portrait_path(
                            base_for_portrait,
                            portrait_path=portrait_path,
                            dest_box_px=dest_box_px,
                            temp_prefix=f"{target.video_id}_base_",
                            anchor=anchor,
                            portrait_zoom=float(portrait_zoom),
                            portrait_offset_px=portrait_offset_px,
                            trim_transparent=bool(trim_transparent),
                            fg_brightness=fg_brightness,
                            fg_contrast=fg_contrast,
                            fg_color=fg_color,
                        ) as base_with_portrait:
                            portrait_used = True
                            base_for_text_out = base_with_portrait
                            if elements_above:
                                base_for_text_out = _apply_elements_to_path(
                                    base_for_text_out,
                                    channel=ch,
                                    video=target.video,
                                    elements=elements_above,
                                    out_path=build_dir / "base__elements_above.png",
                                )
                            if use_canva_text:
                                _compose_text_canva_like(
                                    base_for_text_out,
                                    text_layout_spec=text_spec_for_render,
                                    video_id=target.video_id,
                                    out_path=build_thumb,
                                    output_mode=output_mode,
                                    template_id=template_id,
                                    resolved_text_by_slot=resolved_text_by_slot,
                                    text_line_spec_lines=text_line_spec_lines,
                                    text_offset_x=float(video_text_offset_x),
                                    text_offset_y=float(video_text_offset_y),
                                    template_id_override=template_id_override,
                                    effects_override=effects_override,
                                    overlays_override=overlays_override,
                                )
                            else:
                                compose_text_to_png(
                                    base_for_text_out,
                                    text_layout_spec=text_spec_for_render,
                                    video_id=target.video_id,
                                    out_path=build_thumb,
                                    output_mode=output_mode,
                                    text_override=text_override if text_override else None,
                                    template_id_override=template_id_override,
                                    effects_override=effects_override,
                                    overlays_override=overlays_override,
                                )
            else:
                base_for_text_out = base_for_text
                if elements_below:
                    base_for_text_out = _apply_elements_to_path(
                        base_for_text_out,
                        channel=ch,
                        video=target.video,
                        elements=elements_below,
                        out_path=build_dir / "base__elements_below.png",
                    )
                if elements_above:
                    base_for_text_out = _apply_elements_to_path(
                        base_for_text_out,
                        channel=ch,
                        video=target.video,
                        elements=elements_above,
                        out_path=build_dir / "base__elements_above.png",
                    )
                if use_canva_text:
                    _compose_text_canva_like(
                        base_for_text_out,
                        text_layout_spec=text_spec_for_render,
                        video_id=target.video_id,
                        out_path=build_thumb,
                        output_mode=output_mode,
                        template_id=template_id,
                        resolved_text_by_slot=resolved_text_by_slot,
                        text_line_spec_lines=text_line_spec_lines,
                        text_offset_x=float(video_text_offset_x),
                        text_offset_y=float(video_text_offset_y),
                        template_id_override=template_id_override,
                        effects_override=effects_override,
                        overlays_override=overlays_override,
                    )
                else:
                    compose_text_to_png(
                        base_for_text_out,
                        text_layout_spec=text_spec_for_render,
                        video_id=target.video_id,
                        out_path=build_thumb,
                        output_mode=output_mode,
                        text_override=text_override if text_override else None,
                        template_id_override=template_id_override,
                        effects_override=effects_override,
                        overlays_override=overlays_override,
                    )
        # Update stable artifact (00_thumb.png) from this build output.
        tmp_stable = stable_thumb.with_suffix(stable_thumb.suffix + ".tmp")
        tmp_stable.write_bytes(build_thumb.read_bytes())
        tmp_stable.replace(stable_thumb)

        if flat_out:
            flat_out.write_bytes(stable_thumb.read_bytes())

        title = _resolve_title_from_specs(channel=ch, video_id=target.video_id, image_spec=image_spec, text_spec=text_spec_typed)
        rel_thumb = f"{ch}/{target.video}/{stable_thumb_name}"
        upsert_fs_variant(
            channel=ch,
            video=target.video,
            title=title,
            image_rel_path=rel_thumb,
            label=resolved_variant_label,
            status="review",
        )

        overrides_leaf_json: Dict[str, Any] = {}
        for k, v in overrides_leaf.items():
            if isinstance(v, tuple):
                overrides_leaf_json[k] = list(v)
            else:
                overrides_leaf_json[k] = v

        meta: Dict[str, Any] = {
            "schema": "ytm.thumbnail.layer_specs.build.v1",
            "built_at": datetime.now(timezone.utc).isoformat(),
            "channel": ch,
            "video": target.video,
            "video_id": target.video_id,
            "model_key": model_key,
            "build_id": build_id,
            "output_mode": output_mode,
            "layer_specs": {"image_prompts_id": img_id, "text_layout_id": txt_id},
            "thumb_spec": {
                "path": str(thumb_spec.path.relative_to(fpaths.repo_root())) if thumb_spec else None,
                "overrides_leaf": overrides_leaf_json or None,
            },
            "text": {
                "template_id": template_id,
                "template_id_override": template_id_override,
                "planning_copy": planning_copy,
                "text_override": text_override if text_override else None,
                "effects_override": effects_override,
                "overlays_override": overlays_override,
            },
            "output": {
                "bg_path": str(out_bg.relative_to(fpaths.repo_root())),
                "stable_thumb_path": str(stable_thumb.relative_to(fpaths.repo_root())),
                "build_thumb_path": str(build_thumb.relative_to(fpaths.repo_root())),
                "width": width,
                "height": height,
            },
            "bg_enhance": {
                "brightness": bg_params.brightness,
                "contrast": bg_params.contrast,
                "color": bg_params.color,
                "gamma": bg_params.gamma,
            },
            "bg_pan_zoom": {
                "zoom": float(video_bg_zoom),
                "pan_x": float(video_bg_pan_x),
                "pan_y": float(video_bg_pan_y),
            },
            "bg_enhance_band": {
                "x0": float(video_band_x0),
                "x1": float(video_band_x1),
                "power": float(video_band_power),
                "brightness": band_params.brightness,
                "contrast": band_params.contrast,
                "color": band_params.color,
                "gamma": band_params.gamma,
            },
            "sources": {
                "legacy_moved_from": legacy_moved_from,
                "bg_src": str(bg_src.relative_to(fpaths.repo_root())),
            },
            "portrait": {
                "used": bool(portrait_used),
                "portrait_path": str(portrait_path.relative_to(fpaths.repo_root())) if portrait_used and portrait_path else None,
            },
            "generated": generated,
        }
        tmp_meta = build_meta_path.with_suffix(build_meta_path.suffix + ".tmp")
        tmp_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_meta.replace(build_meta_path)
        print(f"[{idx}/{len(targets)}] {target.video_id}: OK -> {stable_thumb}")

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

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
)
from script_pipeline.thumbnails.layers.text_layer import compose_text_to_png
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
        rows = planning_store.get_rows(ch, force_refresh=True)
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


def _resolve_title_from_specs(
    *,
    channel: str,
    video_id: str,
    image_spec: ImagePromptsSpecV3,
    text_spec: TextLayoutSpecV3,
) -> Optional[str]:
    for item in text_spec.items:
        if str(item.video_id).strip() == str(video_id).strip():
            t = str(item.title).strip()
            if t:
                return t
    for item in image_spec.items:
        if str(item.video_id).strip() == str(video_id).strip():
            t = str(item.title).strip()
            if t:
                return t
    return None


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


def build_channel_thumbnails(
    *,
    channel: str,
    targets: List[BuildTarget],
    width: int,
    height: int,
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
) -> None:
    if bool(regen_bg) and bool(skip_generate):
        raise ValueError("regen_bg cannot be used with skip_generate")
    ch = _normalize_channel(channel)
    img_id, txt_id = resolve_channel_layer_spec_ids(ch)
    if not img_id or not txt_id:
        raise RuntimeError(f"layer_specs not configured for channel: {ch}")
    image_spec = load_image_prompts_v3_typed(img_id)
    text_spec = load_layer_spec_yaml(txt_id)
    text_spec_typed = load_text_layout_v3_typed(txt_id)

    model_key = _resolve_model_key_from_templates(ch)
    if not model_key:
        raise RuntimeError(f"image_model_key not found in workspaces/thumbnails/templates.json for channel={ch}")

    assets_root = fpaths.thumbnails_root() / "assets" / ch
    assets_root.mkdir(parents=True, exist_ok=True)

    client = ImageClient()

    for idx, target in enumerate(targets, start=1):
        video_dir = assets_root / target.video
        video_dir.mkdir(parents=True, exist_ok=True)

        out_bg = video_dir / "10_bg.png"
        out_thumb = video_dir / "00_thumb.png"
        meta_path = video_dir / "meta.json"
        flat_out: Optional[Path] = None
        if export_flat:
            suffix = str(flat_name_suffix or "").strip()
            if suffix and not suffix.startswith("_"):
                suffix = "_" + suffix
            flat_out = assets_root / f"{target.video}{suffix}.png"

        if out_thumb.exists() and not force:
            if flat_out and not flat_out.exists():
                flat_out.write_bytes(out_thumb.read_bytes())
                print(f"[{idx}/{len(targets)}] {target.video_id}: export-flat -> {flat_out.name}")
            else:
                print(f"[{idx}/{len(targets)}] {target.video_id}: skip (already built)")
            continue

        bg_source = resolve_background_source(video_dir=video_dir, channel_root=assets_root, video=target.video)
        bg_src = None if bool(regen_bg) else bg_source.bg_src
        legacy_moved_from = None if bool(regen_bg) else bg_source.legacy_moved_from

        generated: Optional[Dict[str, Any]] = None
        if bg_src is None:
            if skip_generate:
                print(f"[{idx}/{len(targets)}] {target.video_id}: missing bg (skip_generate)")
                continue
            prompt = next((it.prompt_ja for it in image_spec.items if it.video_id == target.video_id), None)
            if not isinstance(prompt, str) or not prompt.strip():
                raise RuntimeError(f"image prompt missing for {target.video_id}")
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
        crop_resize_to_16x9(bg_src, out_bg, width=width, height=height)

        print(f"[{idx}/{len(targets)}] {target.video_id}: composing text ...")
        bg_params = BgEnhanceParams(
            brightness=float(bg_brightness),
            contrast=float(bg_contrast),
            color=float(bg_color),
            gamma=float(bg_gamma),
        )
        band_params = BgEnhanceParams(
            brightness=float(bg_band_brightness),
            contrast=float(bg_band_contrast),
            color=float(bg_band_color),
            gamma=float(bg_band_gamma),
        )
        item = find_text_layout_item_for_video(text_spec, target.video_id) if isinstance(text_spec, dict) else None
        template_id = str(item.get("template_id") or "").strip() if isinstance(item, dict) else ""
        templates = text_spec.get("templates") if isinstance(text_spec, dict) else None
        slots = None
        if template_id and isinstance(templates, dict):
            tpl = templates.get(template_id)
            slots = tpl.get("slots") if isinstance(tpl, dict) else None
        text_payload = item.get("text") if isinstance(item, dict) else None
        planning_copy = _load_planning_copy(ch, target.video)
        text_override: Dict[str, str] = {}
        if planning_copy and isinstance(slots, dict):
            for slot_name in slots.keys():
                # Only override missing slot text (keeps existing CH10 specs intact)
                cur = ""
                if isinstance(text_payload, dict):
                    cur = str(text_payload.get(slot_name) or "").strip()
                if cur:
                    continue
                val = _planning_value_for_slot(slot_name, planning_copy)
                if val:
                    text_override[str(slot_name)] = val

        with enhanced_bg_path(
            out_bg,
            params=bg_params,
            zoom=float(bg_zoom),
            pan_x=float(bg_pan_x),
            pan_y=float(bg_pan_y),
            band_params=band_params,
            band_x0=float(bg_band_x0),
            band_x1=float(bg_band_x1),
            band_power=float(bg_band_power),
            temp_prefix=f"{target.video_id}_bg_",
        ) as base_for_text:
            portrait_path = find_existing_portrait(video_dir)
            portrait_used = False
            if portrait_path is not None:
                # CH26 benchmark: portrait is composited as a separate layer (本人肖像素材を使用)
                dest_box_px = (
                    int(round(width * 0.29)),
                    int(round(height * 0.06)),
                    int(round(width * 0.42)),
                    int(round(height * 0.76)),
                )
                fg_brightness = 1.20
                fg_contrast = 1.08
                fg_color = 0.98
                if ch == "CH26":
                    # User feedback: portrait should be brighter, but avoid double-enhancing in portrait prep.
                    fg_brightness = 1.26
                    fg_contrast = 1.10
                    fg_color = 1.00
                with composited_portrait_path(
                    base_for_text,
                    portrait_path=portrait_path,
                    dest_box_px=dest_box_px,
                    temp_prefix=f"{target.video_id}_base_",
                    fg_brightness=fg_brightness,
                    fg_contrast=fg_contrast,
                    fg_color=fg_color,
                ) as base_with_portrait:
                    portrait_used = True
                    compose_text_to_png(
                        base_with_portrait,
                        text_layout_spec=text_spec,
                        video_id=target.video_id,
                        out_path=out_thumb,
                        text_override=text_override if text_override else None,
                    )
            else:
                compose_text_to_png(
                    base_for_text,
                    text_layout_spec=text_spec,
                    video_id=target.video_id,
                    out_path=out_thumb,
                    text_override=text_override if text_override else None,
                )
        if flat_out:
            flat_out.write_bytes(out_thumb.read_bytes())

        title = _resolve_title_from_specs(channel=ch, video_id=target.video_id, image_spec=image_spec, text_spec=text_spec_typed)
        rel_thumb = f"{ch}/{target.video}/00_thumb.png"
        upsert_fs_variant(channel=ch, video=target.video, title=title, image_rel_path=rel_thumb, label="thumb_00", status="review")

        meta: Dict[str, Any] = {
            "schema": "ytm.thumbnail.layer_specs.build.v1",
            "built_at": datetime.now(timezone.utc).isoformat(),
            "channel": ch,
            "video": target.video,
            "video_id": target.video_id,
            "model_key": model_key,
            "output": {
                "bg_path": str(out_bg.relative_to(fpaths.repo_root())),
                "thumb_path": str(out_thumb.relative_to(fpaths.repo_root())),
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
                "zoom": float(bg_zoom),
                "pan_x": float(bg_pan_x),
                "pan_y": float(bg_pan_y),
            },
            "bg_enhance_band": {
                "x0": float(bg_band_x0),
                "x1": float(bg_band_x1),
                "power": float(bg_band_power),
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
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[{idx}/{len(targets)}] {target.video_id}: OK -> {out_thumb}")

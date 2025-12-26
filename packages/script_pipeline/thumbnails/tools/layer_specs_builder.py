#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image, ImageEnhance

from factory_common import paths as fpaths
from factory_common.image_client import ImageClient, ImageGenerationError, ImageTaskOptions
from script_pipeline.thumbnails.compiler.compose_text_layout import compose_text_layout
from script_pipeline.thumbnails.compiler.layer_specs import (
    find_image_prompt_for_video,
    load_layer_spec_yaml,
    resolve_channel_layer_spec_ids,
)


SUPPORTED_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


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

    img_spec = load_layer_spec_yaml(img_id)
    items = img_spec.get("items")
    if not isinstance(items, list):
        raise RuntimeError("layer_specs.image_prompts items missing")

    vids: List[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        vid = str(item.get("video_id") or "").strip()
        if not vid.startswith(f"{ch}-"):
            continue
        suffix = vid.split("-", 1)[1] if "-" in vid else ""
        if suffix and suffix.isdigit():
            vids.append(suffix.zfill(3))
    if not vids:
        raise RuntimeError(f"no video targets found in layer spec for channel={ch}")
    return [BuildTarget(channel=ch, video=v) for v in sorted(set(vids))]


def crop_resize_to_16x9(src_path: Path, dest_path: Path, *, width: int, height: int) -> None:
    """
    Center-crop to 16:9 then resize to (width,height), and save as PNG.
    """
    with Image.open(src_path) as img:
        img = img.convert("RGBA")
        src_w, src_h = img.size
        target_ratio = width / float(height)
        src_ratio = src_w / float(src_h)

        if abs(src_ratio - target_ratio) > 1e-3:
            if src_ratio > target_ratio:
                new_w = int(src_h * target_ratio)
                left = max(0, (src_w - new_w) // 2)
                img = img.crop((left, 0, left + new_w, src_h))
            else:
                new_h = int(src_w / target_ratio)
                top = max(0, (src_h - new_h) // 2)
                img = img.crop((0, top, src_w, top + new_h))

        img = img.resize((width, height), Image.LANCZOS)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(dest_path, format="PNG", optimize=True)


def _apply_gamma_rgb(img: Image.Image, gamma: float) -> Image.Image:
    g = float(gamma)
    if g <= 0:
        raise ValueError("gamma must be > 0")
    if abs(g - 1.0) < 1e-6:
        return img
    lut = [int(round(((i / 255.0) ** g) * 255.0)) for i in range(256)]
    if img.mode == "RGB":
        return img.point(lut * 3)
    if img.mode == "L":
        return img.point(lut)
    return img.convert("RGB").point(lut * 3)


def apply_bg_enhancements(
    img: Image.Image,
    *,
    brightness: float,
    contrast: float,
    color: float,
    gamma: float,
) -> Image.Image:
    b = float(brightness)
    c = float(contrast)
    s = float(color)
    g = float(gamma)
    if abs(b - 1.0) < 1e-6 and abs(c - 1.0) < 1e-6 and abs(s - 1.0) < 1e-6 and abs(g - 1.0) < 1e-6:
        return img

    rgba = img.convert("RGBA")
    alpha = rgba.getchannel("A")
    rgb = rgba.convert("RGB")

    if abs(g - 1.0) >= 1e-6:
        rgb = _apply_gamma_rgb(rgb, g)
    if abs(b - 1.0) >= 1e-6:
        rgb = ImageEnhance.Brightness(rgb).enhance(b)
    if abs(c - 1.0) >= 1e-6:
        rgb = ImageEnhance.Contrast(rgb).enhance(c)
    if abs(s - 1.0) >= 1e-6:
        rgb = ImageEnhance.Color(rgb).enhance(s)

    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    return out


def _legacy_background_candidates(channel_root: Path, video: str) -> List[Path]:
    out: List[Path] = []
    for ext in sorted(SUPPORTED_EXTS):
        out.append(channel_root / f"{video}{ext}")
    for ext in sorted(SUPPORTED_EXTS):
        out.append(channel_root / f"{int(video)}{ext}")
    return out


def _find_existing_background(video_dir: Path) -> Optional[Path]:
    preferred = [
        video_dir / "10_bg.png",
        video_dir / "10_bg.jpg",
        video_dir / "10_bg.jpeg",
        video_dir / "10_bg.webp",
        video_dir / "90_bg_legacy.png",
        video_dir / "90_bg_legacy.jpg",
        video_dir / "90_bg_legacy.jpeg",
        video_dir / "90_bg_legacy.webp",
    ]
    for p in preferred:
        if p.exists() and p.is_file():
            return p
    for p in sorted(video_dir.iterdir(), key=lambda path: path.as_posix().lower()):
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS:
            return p
    return None


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
    image_spec: Dict[str, Any],
    text_spec: Dict[str, Any],
) -> Optional[str]:
    items = text_spec.get("items")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and str(item.get("video_id") or "").strip() == video_id:
                t = str(item.get("title") or "").strip()
                if t:
                    return t
    items = image_spec.get("items")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and str(item.get("video_id") or "").strip() == video_id:
                t = str(item.get("title") or "").strip()
                if t:
                    return t
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
) -> None:
    ch = _normalize_channel(channel)
    img_id, txt_id = resolve_channel_layer_spec_ids(ch)
    if not img_id or not txt_id:
        raise RuntimeError(f"layer_specs not configured for channel: {ch}")
    image_spec = load_layer_spec_yaml(img_id)
    text_spec = load_layer_spec_yaml(txt_id)

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

        bg_src = _find_existing_background(video_dir)
        legacy_moved_from: Optional[str] = None
        if bg_src is None:
            for legacy in _legacy_background_candidates(assets_root, target.video):
                if legacy.exists() and legacy.is_file():
                    dest = video_dir / "90_bg_legacy.png"
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(legacy), str(dest))
                    bg_src = dest
                    legacy_moved_from = str(legacy)
                    break

        generated: Optional[Dict[str, Any]] = None
        if bg_src is None:
            if skip_generate:
                print(f"[{idx}/{len(targets)}] {target.video_id}: missing bg (skip_generate)")
                continue
            prompt = find_image_prompt_for_video(image_spec, target.video_id)
            if not prompt:
                raise RuntimeError(f"image prompt missing for {target.video_id}")

            result = None
            last_exc: Optional[Exception] = None
            attempts = max(1, int(max_gen_attempts))
            for attempt in range(1, attempts + 1):
                try:
                    print(f"[{idx}/{len(targets)}] {target.video_id}: generating bg via {model_key} (attempt {attempt}/{attempts}) ...")
                    result = client.generate(
                        ImageTaskOptions(
                            task="thumbnail_image_gen",
                            prompt=prompt,
                            aspect_ratio="16:9",
                            n=1,
                            extra={"model_key": model_key, "allow_fallback": False},
                        )
                    )
                    if result.images:
                        break
                    last_exc = RuntimeError("image generation returned no image bytes")
                except ImageGenerationError as exc:
                    last_exc = exc
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                time.sleep(max(0.5, float(sleep_sec)))

            if not result or not result.images:
                msg = f"[{idx}/{len(targets)}] {target.video_id}: generation failed ({last_exc})"
                if continue_on_error:
                    print(msg)
                    continue
                raise RuntimeError(f"image generation failed for {target.video_id}: {last_exc}") from last_exc

            raw_path = video_dir / "90_bg_ai_raw.png"
            raw_path.write_bytes(result.images[0])
            bg_src = raw_path
            generated = {
                "provider": result.provider,
                "model": result.model,
                "model_key": model_key,
                "request_id": result.request_id,
                "metadata": result.metadata,
            }
            time.sleep(max(0.0, float(sleep_sec)))

        if not bg_src:
            raise RuntimeError(f"background source resolution failed for {target.video_id}")
        crop_resize_to_16x9(bg_src, out_bg, width=width, height=height)

        print(f"[{idx}/{len(targets)}] {target.video_id}: composing text ...")
        base_for_text = out_bg
        tmp_bg: Optional[Path] = None
        if any(
            abs(x - 1.0) >= 1e-6
            for x in (
                float(bg_brightness),
                float(bg_contrast),
                float(bg_color),
                float(bg_gamma),
            )
        ):
            bg_img = Image.open(out_bg).convert("RGBA")
            bg_img = apply_bg_enhancements(
                bg_img,
                brightness=float(bg_brightness),
                contrast=float(bg_contrast),
                color=float(bg_color),
                gamma=float(bg_gamma),
            )
            handle = tempfile.NamedTemporaryFile(prefix=f"{target.video_id}_bg_", suffix=".png", delete=False)
            try:
                tmp_bg = Path(handle.name)
            finally:
                handle.close()
            bg_img.save(tmp_bg, format="PNG", optimize=True)
            base_for_text = tmp_bg

        out_img = compose_text_layout(base_for_text, text_layout_spec=text_spec, video_id=target.video_id)
        out_img.save(out_thumb, format="PNG", optimize=True)
        if flat_out:
            flat_out.write_bytes(out_thumb.read_bytes())
        if tmp_bg:
            try:
                tmp_bg.unlink()
            except Exception:
                pass

        title = _resolve_title_from_specs(channel=ch, video_id=target.video_id, image_spec=image_spec, text_spec=text_spec)
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
            "sources": {
                "legacy_moved_from": legacy_moved_from,
                "bg_src": str(bg_src.relative_to(fpaths.repo_root())),
            },
            "generated": generated,
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[{idx}/{len(targets)}] {target.video_id}: OK -> {out_thumb}")


#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from video_pipeline.tools._tool_bootstrap import bootstrap as tool_bootstrap
except Exception:
    from _tool_bootstrap import bootstrap as tool_bootstrap  # type: ignore

tool_bootstrap(load_env=False)


def _write_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, data: Dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _tracks(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    if isinstance(data.get("tracks"), list):
        return data["tracks"]
    script = data.get("script")
    if isinstance(script, dict) and isinstance(script.get("tracks"), list):
        return script["tracks"]
    return []


def _find_track(data: Dict[str, Any], *, track_prefix: str, prefer_len: Optional[int]) -> Optional[Dict[str, Any]]:
    prefix = (track_prefix or "").lower()
    tracks = _tracks(data)
    for t in tracks:
        name = (t.get("name") or t.get("id") or "")
        if isinstance(name, str) and name.lower().startswith(prefix):
            return t
    if prefer_len:
        candidates = [
            t
            for t in tracks
            if t.get("type") == "video" and isinstance(t.get("segments"), list) and len(t.get("segments") or []) == prefer_len
        ]
        if candidates:
            return candidates[0]
    videos = [t for t in tracks if t.get("type") == "video" and isinstance(t.get("segments"), list)]
    if videos:
        videos = sorted(videos, key=lambda x: len(x.get("segments") or []), reverse=True)
        return videos[0]
    return None


def _sync_srt2images_materials(*, draft_dir: Path, track_prefix: str, prefer_len: Optional[int]) -> None:
    content_path = draft_dir / "draft_content.json"
    info_path = draft_dir / "draft_info.json"
    content = _load_json(content_path)
    info = _load_json(info_path)

    ct = _find_track(content, track_prefix=track_prefix, prefer_len=prefer_len)
    it = _find_track(info, track_prefix=track_prefix, prefer_len=prefer_len)
    if not ct or not it:
        raise RuntimeError("target track not found in draft_content/draft_info")

    csegs = ct.get("segments") or []
    isegs = it.get("segments") or []
    limit = min(len(csegs), len(isegs))

    c_vids = (content.get("materials") or {}).get("videos") or []
    by_id = {m.get("id"): m for m in c_vids if isinstance(m, dict)}

    for i in range(limit):
        mid = (csegs[i] or {}).get("material_id")
        if not mid:
            continue
        isegs[i]["material_id"] = mid
        if mid in by_id and by_id[mid].get("material_name"):
            isegs[i]["material_name"] = by_id[mid]["material_name"]

    target_ids = {((csegs[i] or {}).get("material_id") or "") for i in range(limit)}
    target_ids = {x for x in target_ids if x}

    i_mats = info.setdefault("materials", {}).setdefault("videos", [])
    if not isinstance(i_mats, list):
        info["materials"]["videos"] = []
        i_mats = info["materials"]["videos"]

    i_by_id = {m.get("id"): idx for idx, m in enumerate(i_mats) if isinstance(m, dict)}
    for mid in target_ids:
        if mid not in by_id:
            continue
        if mid in i_by_id:
            i_mats[i_by_id[mid]] = by_id[mid]
        else:
            i_mats.append(by_id[mid])

    ts = time.strftime("%Y%m%d_%H%M%S")
    shutil.copy2(info_path, info_path.with_suffix(info_path.suffix + f".bak_vrew_{ts}"))
    _save_json(info_path, info)


def _select_segments(segments: List[Dict[str, Any]], only: str) -> List[Tuple[int, Dict[str, Any]]]:
    out: List[Tuple[int, Dict[str, Any]]] = []
    for i, seg in enumerate(segments):
        status = str(seg.get("status") or "pending")
        if only == "all":
            ok = True
        elif only == "failed":
            ok = status == "failed"
        elif only == "pending":
            ok = status == "pending"
        elif only == "generated":
            ok = status == "generated"
        elif only == "not-placed":
            ok = status != "placed"
        else:
            ok = True
        if ok:
            out.append((i, seg))
    return out


def _resolve_image_path(manifest_dir: Path, image_path: str) -> Path:
    p = Path(image_path)
    if p.is_absolute():
        return p
    return (manifest_dir / p).resolve()


def _copy_asset(src: Path, draft_dir: Path, *, queue_index: int, fmt: str, ts_tag: str) -> Path:
    asset_dir = draft_dir / "assets" / "image"
    asset_dir.mkdir(parents=True, exist_ok=True)
    fname = f"img_{queue_index:04d}_vrew_{ts_tag}.{fmt}"
    dest = asset_dir / fname

    # Convert when needed (e.g. user skipped importer)
    if src.suffix.lower().lstrip(".") == fmt:
        shutil.copy2(src, dest)
        return dest

    from PIL import Image

    pil_fmt = {"png": "PNG", "jpg": "JPEG", "webp": "WEBP"}.get(fmt, "PNG")
    with Image.open(src) as im:
        if pil_fmt == "JPEG" and im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        im.save(dest, format=pil_fmt)
    return dest


def _planned_asset_path(draft_dir: Path, *, queue_index: int, fmt: str, ts_tag: str) -> Path:
    return draft_dir / "assets" / "image" / f"img_{queue_index:04d}_vrew_{ts_tag}.{fmt}"


def _swap_material_id(content: Dict[str, Any], *, old_id: str, new_id: str) -> int:
    replacement_count = 0
    for track in _tracks(content):
        for seg in track.get("segments", []) or []:
            if seg.get("material_id") == old_id:
                seg["material_id"] = new_id
                replacement_count += 1
            refs = seg.get("extra_material_refs")
            if isinstance(refs, list):
                for i, ref in enumerate(refs):
                    if ref == old_id:
                        refs[i] = new_id
                        replacement_count += 1
    return replacement_count


def main() -> None:
    ap = argparse.ArgumentParser(description="Place images into an existing CapCut draft based on image_manifest.json (Vrew route)")
    ap.add_argument("--manifest", required=True, help="Path to image_manifest.json")
    ap.add_argument("--draft", required=True, help="CapCut draft directory (contains draft_content.json)")
    ap.add_argument("--track-prefix", default="srt2images_", help="Target track name/id prefix (default: srt2images_)")
    ap.add_argument("--only", choices=["all", "failed", "pending", "generated", "not-placed"], default="not-placed", help="Which segments to place")
    ap.add_argument("--only-allow-draft-substring", help="If set, draft path must contain this substring (safety)")
    ap.add_argument("--no-backup", action="store_true", help="Disable full draft directory backup (not recommended)")
    ap.add_argument("--apply", action="store_true", help="Actually write changes (default: dry-run)")
    args = ap.parse_args()

    manifest_path = Path(args.manifest).expanduser().resolve()
    draft_dir = Path(args.draft).expanduser().resolve()
    if not manifest_path.exists():
        raise SystemExit(f"❌ manifest not found: {manifest_path}")
    if not draft_dir.exists():
        raise SystemExit(f"❌ draft dir not found: {draft_dir}")
    if args.only_allow_draft_substring and args.only_allow_draft_substring not in draft_dir.name:
        raise SystemExit(
            f"❌ draft path '{draft_dir}' does not contain required substring '{args.only_allow_draft_substring}'. Aborting."
        )

    manifest_dir = manifest_path.parent
    logs_dir = manifest_dir / "logs"
    run_log = logs_dir / f"run_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"

    manifest = _load_json(manifest_path)
    segments = manifest.get("segments") or []
    if not isinstance(segments, list):
        raise SystemExit("❌ invalid manifest: segments must be a list")

    fmt = str(((manifest.get("image_spec") or {}).get("format") or "png")).lower().strip(".")
    if fmt == "jpeg":
        fmt = "jpg"
    if fmt not in {"png", "jpg", "webp"}:
        fmt = "png"

    prefer_len = len(segments) if segments else None

    # Preload draft json (dry-run still needs track detection)
    content_path = draft_dir / "draft_content.json"
    info_path = draft_dir / "draft_info.json"
    if not content_path.exists() or not info_path.exists():
        raise SystemExit("❌ draft_content.json or draft_info.json missing")

    content = _load_json(content_path)
    content_track = _find_track(content, track_prefix=args.track_prefix, prefer_len=prefer_len)
    if not content_track:
        raise SystemExit("❌ target video track not found in draft_content.json")
    content_segments = content_track.get("segments") or []

    materials = (content.get("materials") or {}).get("videos") or []
    if not isinstance(materials, list):
        raise SystemExit("❌ draft_content.json materials.videos is not a list")

    by_id: Dict[str, Dict[str, Any]] = {m.get("id"): m for m in materials if isinstance(m, dict) and isinstance(m.get("id"), str)}

    selected = _select_segments(segments, args.only)

    _write_jsonl(
        run_log,
        {
            "ts": time.time(),
            "event": "place_images_to_capcut_start",
            "project_id": manifest.get("project_id"),
            "manifest": str(manifest_path),
            "draft": str(draft_dir),
            "track_prefix": args.track_prefix,
            "prefer_len": prefer_len,
            "only": args.only,
            "apply": bool(args.apply),
        },
    )

    ts_tag = time.strftime("%Y%m%d_%H%M%S")
    planned_backup_dir = draft_dir.parent / f"{draft_dir.name}_vrew_bak_{ts_tag}"

    plan: List[Dict[str, Any]] = []
    ok_count = 0
    fail_count = 0

    content_mut = content if args.apply else copy.deepcopy(content)
    content_track_mut = _find_track(content_mut, track_prefix=args.track_prefix, prefer_len=prefer_len) or {}
    content_segments_mut = content_track_mut.get("segments") or []
    materials_mut = ((content_mut.get("materials") or {}).get("videos") or [])
    by_id_mut: Dict[str, Dict[str, Any]] = {
        m.get("id"): m for m in materials_mut if isinstance(m, dict) and isinstance(m.get("id"), str)
    }

    for list_index, seg in selected:
        q = int(seg.get("queue_index") or (list_index + 1))
        idx0 = q - 1
        image_p = _resolve_image_path(manifest_dir, str(seg.get("image_path") or f"images/img_{q:04d}.{fmt}"))

        if not image_p.exists():
            seg["status"] = "failed"
            seg["error"] = f"image_missing:{image_p}"
            fail_count += 1
            continue

        if idx0 < 0 or idx0 >= len(content_segments_mut):
            seg["status"] = "failed"
            seg["error"] = f"queue_out_of_range:{q}"
            fail_count += 1
            continue

        old_id = (content_segments_mut[idx0] or {}).get("material_id")
        if not isinstance(old_id, str) or not old_id:
            seg["status"] = "failed"
            seg["error"] = f"missing_material_id_at_queue:{q}"
            fail_count += 1
            continue

        mat = by_id_mut.get(old_id)
        if not mat:
            seg["status"] = "failed"
            seg["error"] = f"material_not_found:{old_id}"
            fail_count += 1
            continue

        new_id = str(uuid.uuid4())
        dest_asset = (
            _copy_asset(image_p, draft_dir, queue_index=q, fmt=fmt, ts_tag=ts_tag)
            if args.apply
            else _planned_asset_path(draft_dir, queue_index=q, fmt=fmt, ts_tag=ts_tag)
        )

        # Update material definition
        mat["id"] = new_id
        mat["path"] = str(dest_asset)
        mat["material_name"] = dest_asset.name
        try:
            from PIL import Image

            with Image.open(dest_asset) as im:
                mat["width"] = int(im.width)
                mat["height"] = int(im.height)
        except Exception:
            pass

        replaced = _swap_material_id(content_mut, old_id=old_id, new_id=new_id)

        plan.append(
            {
                "queue_index": q,
                "image": str(image_p),
                "draft_asset": str(dest_asset),
                "old_id": old_id,
                "new_id": new_id,
                "replaced_refs": replaced,
            }
        )
        seg["status"] = "placed" if args.apply else seg.get("status", "generated")
        seg["error"] = None
        ok_count += 1

    if not args.apply:
        print("🔍 DRY-RUN (use --apply to actually write)")
        print(f"🔍 would backup draft -> {planned_backup_dir}" if not args.no_backup else "🔍 (backup disabled)")
        print(f"🔍 would update: {content_path}")
        print(f"🔍 would sync: {info_path}")
        print(f"🔍 would update manifest: {manifest_path}")
        print(f"📝 plan items: {len(plan)} ok={ok_count} failed={fail_count}")
        _write_jsonl(
            run_log,
            {
                "ts": time.time(),
                "event": "place_images_to_capcut_dry_run",
                "ok": ok_count,
                "failed": fail_count,
                "planned_backup_dir": str(planned_backup_dir) if not args.no_backup else None,
                "plan_items": len(plan),
            },
        )
        print(f"📝 log: {run_log}")
        return

    # Apply: backup full draft dir first
    if not args.no_backup:
        shutil.copytree(draft_dir, planned_backup_dir)

    # Backup and write draft_content.json
    shutil.copy2(content_path, content_path.with_suffix(content_path.suffix + f".bak_vrew_{ts_tag}"))
    _save_json(content_path, content_mut)

    # Sync draft_info.json
    _sync_srt2images_materials(draft_dir=draft_dir, track_prefix=args.track_prefix, prefer_len=prefer_len)

    # Backup and update manifest
    manifest_backup = manifest_path.with_suffix(manifest_path.suffix + f".bak_{ts_tag}")
    shutil.copy2(manifest_path, manifest_backup)
    _save_json(manifest_path, manifest)

    _write_jsonl(
        run_log,
        {
            "ts": time.time(),
            "event": "place_images_to_capcut_done",
            "ok": ok_count,
            "failed": fail_count,
            "draft_backup_dir": str(planned_backup_dir) if not args.no_backup else None,
            "manifest": str(manifest_path),
            "manifest_backup": str(manifest_backup),
            "plan_items": len(plan),
        },
    )

    print(f"✅ updated: {content_path}")
    print(f"✅ synced: {info_path}")
    print(f"✅ updated: {manifest_path}")
    if not args.no_backup:
        print(f"🛡️ backup: {planned_backup_dir}")
    print(f"📝 log: {run_log}")
    print(f"📌 placed: ok={ok_count} failed={fail_count}")


if __name__ == "__main__":
    main()

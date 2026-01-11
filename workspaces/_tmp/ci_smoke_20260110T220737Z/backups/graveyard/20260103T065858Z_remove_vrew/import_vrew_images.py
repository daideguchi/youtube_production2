#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from video_pipeline.tools._tool_bootstrap import bootstrap as tool_bootstrap
except Exception:
    from _tool_bootstrap import bootstrap as tool_bootstrap  # type: ignore

tool_bootstrap(load_env=False)


_RE_INDEX_4D = re.compile(r"(\d{4})")


def _write_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _load_manifest(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "segments" not in data:
        raise ValueError("invalid manifest: missing top-level 'segments'")
    if not isinstance(data.get("segments"), list):
        raise ValueError("invalid manifest: 'segments' must be a list")
    return data


def _save_manifest(path: Path, data: Dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _collect_images(src_dir: Path) -> List[Path]:
    exts = {".png", ".jpg", ".jpeg", ".webp"}
    files = [p for p in src_dir.iterdir() if p.is_file() and p.suffix.lower() in exts]
    # Natural-ish: (stem, suffix) sorted
    return sorted(files, key=lambda p: (p.stem.lower(), p.suffix.lower()))


def _extract_queue_index(path: Path) -> Optional[int]:
    m = _RE_INDEX_4D.search(path.stem)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _dest_format(manifest: Dict[str, Any]) -> str:
    fmt = ((manifest.get("image_spec") or {}).get("format") or "png")
    fmt = str(fmt).lower().strip(".")
    if fmt == "jpeg":
        fmt = "jpg"
    if fmt not in {"png", "jpg", "webp"}:
        fmt = "png"
    return fmt


def _convert_or_copy(src: Path, dest: Path, *, convert: bool) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not convert:
        shutil.copy2(src, dest)
        return

    from PIL import Image

    fmt = dest.suffix.lower().lstrip(".")
    if fmt == "jpg":
        pil_fmt = "JPEG"
    elif fmt == "png":
        pil_fmt = "PNG"
    elif fmt == "webp":
        pil_fmt = "WEBP"
    else:
        pil_fmt = "PNG"

    with Image.open(src) as im:
        if pil_fmt == "JPEG" and im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        im.save(dest, format=pil_fmt)


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
        elif only == "not-placed":
            ok = status != "placed"
        else:
            ok = True
        if ok:
            out.append((i, seg))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Import Vrew-exported images into images/ and update image_manifest.json")
    ap.add_argument("--manifest", required=True, help="Path to image_manifest.json")
    ap.add_argument("--from-vrew", required=True, help="Directory that contains Vrew-exported images")
    ap.add_argument("--only", choices=["all", "failed", "pending", "not-placed"], default="all", help="Which segments to update")
    ap.add_argument("--no-convert", action="store_true", help="Copy as-is (no format conversion). Not recommended.")
    ap.add_argument("--reset-placed", action="store_true", help="If set, also set status=generated for already placed segments")
    args = ap.parse_args()

    manifest_path = Path(args.manifest).expanduser().resolve()
    from_dir = Path(args.from_vrew).expanduser().resolve()
    if not manifest_path.exists():
        raise SystemExit(f"❌ manifest not found: {manifest_path}")
    if not from_dir.exists():
        raise SystemExit(f"❌ from-vrew dir not found: {from_dir}")

    manifest = _load_manifest(manifest_path)
    segments = manifest["segments"]
    fmt = _dest_format(manifest)

    base_dir = manifest_path.parent
    logs_dir = base_dir / "logs"
    run_log = logs_dir / f"run_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"

    images = _collect_images(from_dir)
    index_map: Dict[int, Path] = {}
    for p in images:
        qi = _extract_queue_index(p)
        if qi is None:
            continue
        if qi not in index_map:
            index_map[qi] = p

    selected = _select_segments(segments, args.only)

    _write_jsonl(
        run_log,
        {
            "ts": time.time(),
            "event": "import_vrew_images_start",
            "manifest": str(manifest_path),
            "from_vrew": str(from_dir),
            "files_found": len(images),
            "files_with_index": len(index_map),
            "only": args.only,
            "convert": not args.no_convert,
        },
    )

    ok_count = 0
    fail_count = 0
    for list_index, seg in selected:
        q = int(seg.get("queue_index") or (list_index + 1))
        status_before = str(seg.get("status") or "pending")
        if status_before == "placed" and not args.reset_placed:
            continue

        src = index_map.get(q)
        if src is None:
            # Fallback to order-based
            if 0 <= q - 1 < len(images):
                src = images[q - 1]

        if src is None or not src.exists():
            seg["status"] = "failed"
            seg["error"] = f"missing_image_for_queue:{q}"
            fail_count += 1
            continue

        image_rel = str(seg.get("image_path") or f"images/img_{q:04d}.{fmt}")
        dest = (base_dir / image_rel).resolve()
        # Force target extension to match manifest image_spec.format
        dest = dest.with_suffix("." + fmt)
        seg["image_path"] = str(dest.relative_to(base_dir)) if dest.is_relative_to(base_dir) else str(dest)

        try:
            _convert_or_copy(src, dest, convert=not args.no_convert)
            seg["status"] = "generated"
            seg["error"] = None
            ok_count += 1
        except Exception as e:
            seg["status"] = "failed"
            seg["error"] = f"import_error:{type(e).__name__}:{e}"
            fail_count += 1

    ts = time.strftime("%Y%m%d_%H%M%S")
    backup_path = manifest_path.with_suffix(manifest_path.suffix + f".bak_{ts}")
    shutil.copy2(manifest_path, backup_path)
    _save_manifest(manifest_path, manifest)

    _write_jsonl(
        run_log,
        {
            "ts": time.time(),
            "event": "import_vrew_images_done",
            "ok": ok_count,
            "failed": fail_count,
            "manifest": str(manifest_path),
            "manifest_backup": str(backup_path),
        },
    )

    print(f"✅ updated: {manifest_path}")
    print(f"🛟 backup: {backup_path}")
    print(f"📝 log: {run_log}")
    print(f"📦 imported: ok={ok_count} failed={fail_count}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Fill duplicate srt2images segments with additional (free) stock-derived images.

Use-case:
  When image intervals are shortened (e.g., 15–25s), some episodes may require
  more unique image materials than the original count. This tool:
    - Downloads free stock b-roll videos (Pexels/Pixabay/Coverr)
    - Extracts 1920x1080 frames via ffmpeg
    - Adds them as new photo materials in the CapCut draft
    - Replaces duplicated srt2images segment material_id references

Edits:
  - draft_content.json
  - draft_info.json

No external LLM / paid image generation is used.
"""

from __future__ import annotations

import argparse
import copy
import json
import random
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from factory_common.paths import video_state_root
from video_pipeline.src.stock_broll.fetcher import fetch_best_stock_video


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _find_srt2images_track(data: dict) -> dict | None:
    tracks = data.get("tracks") or data.get("script", {}).get("tracks") or []
    if isinstance(tracks, dict):
        tracks = list(tracks.values())
    if not isinstance(tracks, list):
        return None
    for track in tracks:
        name = (track.get("name") or track.get("id") or "").lower()
        if name.startswith("srt2images_") and track.get("type") == "video":
            return track
    return None


def _derive_queries(title: str) -> list[str]:
    t = str(title or "")
    queries: list[str] = []

    if any(k in t for k in ("風水", "邪気", "浄化", "捨て", "エントロピー")):
        queries += [
            "decluttering room",
            "minimalist room",
            "cleaning home",
            "feng shui",
            "home organization",
        ]
    if any(k in t for k in ("脳", "神経", "前頭", "潜在意識")):
        queries += [
            "brain",
            "neurons",
            "neuroscience",
            "abstract network",
            "mindfulness",
        ]
    if any(k in t for k in ("アファメーション", "睡眠", "思い込み")):
        queries += [
            "sleep",
            "night sky",
            "meditation",
            "calm waves",
        ]
    if any(k in t for k in ("チャクラ", "オーラ")):
        queries += [
            "chakra",
            "aura",
            "energy meditation",
            "yoga meditation",
        ]

    queries += [
        "meditation",
        "mindfulness",
        "calm nature",
        "abstract background",
        "sunrise",
        "ocean waves",
        "forest light",
    ]

    out: list[str] = []
    seen = set()
    for q in queries:
        qn = re.sub(r"\s+", " ", q.strip().lower())
        if not qn or qn in seen:
            continue
        seen.add(qn)
        out.append(q.strip())
    return out or ["meditation"]


def _next_image_index(assets_dir: Path) -> int:
    max_idx = 0
    for p in assets_dir.glob("*.png"):
        m = re.match(r"^(\d{4})_", p.name)
        if not m:
            continue
        try:
            max_idx = max(max_idx, int(m.group(1)))
        except Exception:
            continue
    return max_idx + 1


def _make_image_filename(index: int) -> str:
    return f"{index:04d}_v{int(time.time())}.png"


def _ffmpeg_extract_frame(*, mp4_path: Path, out_png: Path, t_sec: float) -> None:
    out_png.parent.mkdir(parents=True, exist_ok=True)
    vf = "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080"
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        str(max(0.0, float(t_sec))),
        "-i",
        str(mp4_path),
        "-vframes",
        "1",
        "-vf",
        vf,
        str(out_png),
    ]
    subprocess.run(cmd, check=True)


def _pick_base_photo_material(materials_videos: list[dict]) -> dict:
    for m in materials_videos:
        if isinstance(m, dict) and m.get("type") == "photo" and m.get("path"):
            return m
    raise ValueError("No base photo material found")


def _ensure_material_in_list(materials_videos: list[dict], mat: dict) -> None:
    mid = mat.get("id")
    if not mid:
        raise ValueError("material missing id")
    for i, existing in enumerate(materials_videos):
        if isinstance(existing, dict) and existing.get("id") == mid:
            materials_videos[i] = mat
            return
    materials_videos.append(mat)


def _find_duplicates_indices(material_ids: list[str], *, keep_first: int) -> list[int]:
    seen = set()
    dup = []
    for i, mid in enumerate(material_ids):
        if i < keep_first:
            seen.add(mid)
            continue
        if mid in seen:
            dup.append(i)
        else:
            seen.add(mid)
    return dup


def main() -> int:
    ap = argparse.ArgumentParser(description="Fill duplicate srt2images segments with extra stock images (no LLM)")
    ap.add_argument("--draft", action="append", required=True, help="CapCut draft dir (repeatable)")
    ap.add_argument("--providers", default="pexels,pixabay,coverr", help="Comma-separated providers to try")
    ap.add_argument("--keep-first", type=int, default=2, help="Do not touch first N segments")
    ap.add_argument("--max-new", type=int, default=0, help="Limit new images per draft (0=auto)")
    ap.add_argument("--min-bytes", type=int, default=80_000, help="Min bytes for extracted png sanity check")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    providers = [p.strip().lower() for p in str(args.providers).split(",") if p.strip()]
    if not providers:
        print("❌ no providers specified")
        return 2

    tmp_root = video_state_root() / "tmp_stock_frames"
    tmp_root.mkdir(parents=True, exist_ok=True)

    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

    for draft_str in args.draft:
        draft_dir = Path(draft_str).expanduser()
        if not draft_dir.exists():
            print(f"❌ draft not found: {draft_dir}")
            return 2

        content_path = draft_dir / "draft_content.json"
        info_path = draft_dir / "draft_info.json"
        if not content_path.exists() or not info_path.exists():
            print(f"❌ draft_content.json or draft_info.json missing: {draft_dir}")
            return 2

        content = _load_json(content_path)
        info = _load_json(info_path)
        ctrack = _find_srt2images_track(content)
        itrack = _find_srt2images_track(info)
        if not ctrack or not itrack:
            print(f"❌ srt2images track not found: {draft_dir}")
            return 2

        csegs = list(ctrack.get("segments") or [])
        isegs = list(itrack.get("segments") or [])
        if len(csegs) != len(isegs):
            print(f"❌ segment count mismatch: content={len(csegs)} info={len(isegs)} ({draft_dir})")
            return 2

        material_ids: list[str] = []
        for seg in isegs:
            mid = seg.get("material_id")
            if not isinstance(mid, str) or not mid:
                print(f"❌ missing material_id in segment ({draft_dir})")
                return 2
            material_ids.append(mid)

        dup_indices = _find_duplicates_indices(material_ids, keep_first=max(0, int(args.keep_first)))
        if args.max_new and args.max_new > 0:
            dup_indices = dup_indices[: int(args.max_new)]

        if not dup_indices:
            print(f"✅ {draft_dir.name}: no duplicates to fill")
            continue

        assets_dir = draft_dir / "assets" / "image"
        if not assets_dir.exists():
            print(f"❌ assets/image not found: {draft_dir}")
            return 2

        content_mats = content.setdefault("materials", {}).setdefault("videos", [])
        info_mats = info.setdefault("materials", {}).setdefault("videos", [])
        if not isinstance(content_mats, list) or not isinstance(info_mats, list):
            print(f"❌ materials.videos invalid: {draft_dir}")
            return 2

        base_photo = _pick_base_photo_material(content_mats)
        queries = _derive_queries(draft_dir.name)

        if args.dry_run:
            print(f"[DRY] {draft_dir.name}: duplicates={len(dup_indices)} keep_first={args.keep_first}")
            continue

        shutil.copy2(content_path, str(content_path) + f".bak_dupfills_{ts}")
        shutil.copy2(info_path, str(info_path) + f".bak_dupfills_{ts}")

        rng = random.Random(abs(hash(draft_dir.name)) % (2**31))
        next_idx = _next_image_index(assets_dir)
        added = 0

        for n, seg_i in enumerate(dup_indices):
            q0 = queries[(n + rng.randrange(0, 3)) % len(queries)]
            query_variants = [q0] + [queries[(n + k) % len(queries)] for k in range(1, min(4, len(queries)))]

            out_png = assets_dir / _make_image_filename(next_idx)
            next_idx += 1

            safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", draft_dir.name)[:40] or "draft"
            tmp_mp4 = tmp_root / f"{safe_name}_{seg_i}_{uuid.uuid4().hex}.mp4"

            got = None
            meta = None
            for q in query_variants:
                for prov in providers:
                    try:
                        res = fetch_best_stock_video(
                            provider=prov,
                            query=q,
                            out_path=tmp_mp4,
                            desired_duration_sec=20.0,
                            max_duration_sec=120.0,
                            min_w=1280,
                            min_h=720,
                            prefer_ar="16:9",
                        )
                    except Exception:
                        res = None
                    if not res:
                        continue
                    got, meta = res
                    break
                if got:
                    break

            if not got or not meta:
                print(f"❌ failed to fetch stock video for {draft_dir.name} seg#{seg_i}")
                return 2

            dur = float(meta.get("duration_sec") or 20.0)
            t_sec = max(1.0, min(dur - 1.0, dur * (0.25 + 0.5 * rng.random()))) if dur > 2.0 else 0.0

            try:
                _ffmpeg_extract_frame(mp4_path=got, out_png=out_png, t_sec=t_sec)
            except Exception as e:
                print(f"❌ ffmpeg extract failed: {e} ({draft_dir.name})")
                return 2
            finally:
                try:
                    if tmp_mp4.exists():
                        tmp_mp4.unlink()
                except Exception:
                    pass

            try:
                if not out_png.exists() or out_png.stat().st_size < int(args.min_bytes):
                    print(f"❌ extracted png too small: {out_png} ({draft_dir.name})")
                    return 2
            except Exception:
                print(f"❌ extracted png stat failed: {out_png} ({draft_dir.name})")
                return 2

            new_mat = copy.deepcopy(base_photo)
            new_id = str(uuid.uuid4())
            new_mat["id"] = new_id
            new_mat["material_id"] = uuid.uuid4().hex
            new_mat["material_name"] = out_png.name
            new_mat["path"] = str(out_png)
            new_mat["width"] = 1920
            new_mat["height"] = 1080

            _ensure_material_in_list(content_mats, new_mat)
            _ensure_material_in_list(info_mats, copy.deepcopy(new_mat))

            csegs[seg_i]["material_id"] = new_id
            isegs[seg_i]["material_id"] = new_id
            isegs[seg_i]["material_name"] = out_png.name

            added += 1

        ctrack["segments"] = csegs
        itrack["segments"] = isegs

        _save_json(content_path, content)
        _save_json(info_path, info)

        print(f"✅ {draft_dir.name}: added {added} new images; duplicates fixed")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


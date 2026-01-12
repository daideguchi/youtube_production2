#!/usr/bin/env python3
"""
video_assets_pack.py — 編集ソフト非依存の「Episode Asset Pack」を作る/同期する

SSOT:
  - ssot/ops/OPS_VIDEO_ASSET_PACK.md

SoT（Git追跡）:
  - workspaces/video/assets/episodes/{CHxx}/{NNN}/
    - images/0001.png ...
    - audio/CHxx-NNN.wav (optional)
    - subtitles/CHxx-NNN.srt (optional)
    - manifest.json

このツールは「CapCutを使う/使わない」を問わず、外部作業者が同じ素材束をWebから取得できるようにする。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from _bootstrap import bootstrap


VIDEO_ID_RE = re.compile(r"^(CH\d{2})-(\d{3})\b", flags=re.IGNORECASE)
IMG_RE = re.compile(r"^(\d{1,4})\.(png|jpg|jpeg|webp)$", flags=re.IGNORECASE)


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _norm_channel(raw: str) -> str:
    s = str(raw or "").strip().upper()
    m = re.fullmatch(r"CH(\d{1,3})", s)
    if m:
        return f"CH{int(m.group(1)):02d}"
    return s


def _norm_video(raw: str) -> str:
    s = str(raw or "").strip()
    if re.fullmatch(r"\d{3}", s):
        return s
    try:
        return f"{int(s):03d}"
    except Exception:
        return s


def _video_id(channel: str, video: str) -> str:
    return f"{_norm_channel(channel)}-{_norm_video(video)}"


def _parse_video_id_from_run_name(run_name: str) -> tuple[str, str] | None:
    m = VIDEO_ID_RE.match(str(run_name or "").strip())
    if not m:
        return None
    return (_norm_channel(m.group(1)), _norm_video(m.group(2)))


def _iter_dirs(root: Path) -> Iterable[Path]:
    if not root.exists():
        return []
    return (p for p in root.iterdir() if p.is_dir())


def _find_latest_run_dir(channel: str, video: str) -> Path | None:
    from factory_common import paths as repo_paths

    ch = _norm_channel(channel)
    vv = _norm_video(video)
    prefix = f"{ch}-{vv}"
    root = repo_paths.video_runs_root()
    candidates: list[Path] = []
    for p in _iter_dirs(root):
        if not p.name.upper().startswith(prefix):
            continue
        if not (p / "image_cues.json").exists() and not (p / "images").exists():
            continue
        candidates.append(p)
    if not candidates:
        return None
    candidates.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return candidates[0]


def _collect_run_images(run_dir: Path) -> list[tuple[int, Path]]:
    images_dir = run_dir / "images"
    if not images_dir.exists():
        return []
    out: list[tuple[int, Path]] = []
    for p in sorted(images_dir.iterdir()):
        if not p.is_file():
            continue
        m = IMG_RE.match(p.name)
        if not m:
            continue
        idx = int(m.group(1))
        ext = str(m.group(2)).lower()
        if ext != "png":
            continue
        out.append((idx, p))
    return out


def _collect_src_images(src_dir: Path) -> list[tuple[int, Path]]:
    if not src_dir.exists():
        return []
    out: list[tuple[int, Path]] = []
    for p in sorted(src_dir.iterdir()):
        if not p.is_file():
            continue
        m = IMG_RE.match(p.name)
        if not m:
            continue
        idx = int(m.group(1))
        ext = str(m.group(2)).lower()
        if ext != "png":
            continue
        out.append((idx, p))
    return out


def _copy_file(src: Path, dest: Path, *, overwrite: bool, write: bool) -> None:
    if dest.exists() and not overwrite:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not write:
        return
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    shutil.copy2(src, tmp)
    tmp.replace(dest)


@dataclass(frozen=True)
class Manifest:
    schema: str
    generated_at: str
    video_id: str
    channel: str
    video: str
    sources: dict[str, str]
    files: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "generated_at": self.generated_at,
            "video_id": self.video_id,
            "channel": self.channel,
            "video": self.video,
            "sources": self.sources,
            "files": self.files,
        }


def _write_manifest(dest_dir: Path, manifest: Manifest, *, write: bool) -> None:
    path = dest_dir / "manifest.json"
    payload = json.dumps(manifest.to_json(), ensure_ascii=False, indent=2) + "\n"
    if not write:
        return
    dest_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def _export_audio_and_srt(channel: str, video: str, dest_dir: Path, *, overwrite: bool, write: bool) -> dict[str, Any]:
    from factory_common import paths as repo_paths

    ch = _norm_channel(channel)
    vv = _norm_video(video)
    out: dict[str, Any] = {"wav": None, "srt": None}

    audio_dir = repo_paths.audio_final_dir(ch, vv)
    wav = audio_dir / f"{ch}-{vv}.wav"
    srt = audio_dir / f"{ch}-{vv}.srt"

    if wav.exists():
        _copy_file(wav, dest_dir / "audio" / wav.name, overwrite=overwrite, write=write)
        out["wav"] = f"audio/{wav.name}"
    if srt.exists():
        _copy_file(srt, dest_dir / "subtitles" / srt.name, overwrite=overwrite, write=write)
        out["srt"] = f"subtitles/{srt.name}"
    return out


def cmd_export(args: argparse.Namespace) -> int:
    from factory_common import paths as repo_paths

    write = bool(args.write)
    overwrite = bool(args.overwrite)

    run_dir: Path | None = Path(args.run).expanduser().resolve() if args.run else None
    if run_dir and not run_dir.exists():
        raise SystemExit(f"run_dir not found: {run_dir}")

    channel = _norm_channel(args.channel or "")
    video = _norm_video(args.video or "")

    if run_dir and (not channel or not video):
        parsed = _parse_video_id_from_run_name(run_dir.name)
        if parsed:
            channel, video = parsed
    if not channel or not video:
        raise SystemExit("missing --channel/--video (or provide --run with CHxx-NNN prefix)")

    if not run_dir:
        run_dir = _find_latest_run_dir(channel, video)
    if not run_dir:
        raise SystemExit(f"no run_dir found for {_video_id(channel, video)} under {repo_paths.video_runs_root()}")

    dest_dir = repo_paths.video_episode_assets_dir(channel, video)
    images = _collect_run_images(run_dir)

    copied: list[str] = []
    for idx, src in images:
        rel = f"images/{idx:04d}.png"
        _copy_file(src, dest_dir / rel, overwrite=overwrite, write=write)
        copied.append(rel)

    audio_meta: dict[str, Any] = {}
    if bool(args.include_audio):
        audio_meta = _export_audio_and_srt(channel, video, dest_dir, overwrite=overwrite, write=write)

    sources = {
        "run_dir": str(run_dir),
        "asset_pack_dir": str(dest_dir),
    }
    manifest = Manifest(
        schema="ytm.video.episode_asset_pack.v1",
        generated_at=_now_iso_utc(),
        video_id=_video_id(channel, video),
        channel=channel,
        video=video,
        sources=sources,
        files={
            "images": {"count": len(copied), "items": copied},
            "audio": audio_meta,
        },
    )
    _write_manifest(dest_dir, manifest, write=write)

    mode = "WRITE" if write else "DRY"
    print(f"[video_assets_pack] mode={mode} export {_video_id(channel, video)} run={run_dir.name}")
    print(f"- dest: {dest_dir}")
    print(f"- images_copied: {len(copied)}")
    if bool(args.include_audio):
        print(f"- audio: wav={bool(audio_meta.get('wav'))} srt={bool(audio_meta.get('srt'))}")
    if not images:
        print("[WARN] no run_dir images found (expected run_dir/images/0001.png ...)")
    return 0


def cmd_ingest_images(args: argparse.Namespace) -> int:
    from factory_common import paths as repo_paths

    write = bool(args.write)
    overwrite = bool(args.overwrite)

    channel = _norm_channel(args.channel or "")
    video = _norm_video(args.video or "")
    if not channel or not video:
        raise SystemExit("missing --channel/--video")

    src_dir = Path(args.src).expanduser().resolve()
    if not src_dir.exists():
        raise SystemExit(f"src dir not found: {src_dir}")

    dest_dir = repo_paths.video_episode_assets_dir(channel, video)
    images = _collect_src_images(src_dir)
    if not images:
        raise SystemExit("no images found (expect 0001.png ... in --src)")

    copied: list[str] = []
    for idx, src in images:
        rel = f"images/{idx:04d}.png"
        _copy_file(src, dest_dir / rel, overwrite=overwrite, write=write)
        copied.append(rel)

    sources = {
        "src_images_dir": str(src_dir),
        "asset_pack_dir": str(dest_dir),
    }
    manifest = Manifest(
        schema="ytm.video.episode_asset_pack.v1",
        generated_at=_now_iso_utc(),
        video_id=_video_id(channel, video),
        channel=channel,
        video=video,
        sources=sources,
        files={
            "images": {"count": len(copied), "items": copied},
            "audio": {},
        },
    )
    _write_manifest(dest_dir, manifest, write=write)

    mode = "WRITE" if write else "DRY"
    print(f"[video_assets_pack] mode={mode} ingest-images {_video_id(channel, video)}")
    print(f"- src: {src_dir}")
    print(f"- dest: {dest_dir}")
    print(f"- images_copied: {len(copied)}")
    return 0


def cmd_sync_to_run(args: argparse.Namespace) -> int:
    from factory_common import paths as repo_paths

    apply = bool(args.apply)
    overwrite = bool(args.overwrite)

    run_dir = Path(args.run).expanduser().resolve()
    if not run_dir.exists():
        raise SystemExit(f"run_dir not found: {run_dir}")

    channel = _norm_channel(args.channel or "")
    video = _norm_video(args.video or "")
    if not channel or not video:
        parsed = _parse_video_id_from_run_name(run_dir.name)
        if parsed:
            channel, video = parsed
    if not channel or not video:
        raise SystemExit("missing --channel/--video (or provide --run with CHxx-NNN prefix)")

    pack_dir = repo_paths.video_episode_assets_dir(channel, video)
    src_images = _collect_src_images(pack_dir / "images")
    if not src_images:
        raise SystemExit(f"no images in asset pack: {pack_dir / 'images'}")

    dest_images_dir = run_dir / "images"
    copied = 0
    for idx, src in src_images:
        dest = dest_images_dir / f"{idx:04d}.png"
        if dest.exists() and not overwrite:
            continue
        dest_images_dir.mkdir(parents=True, exist_ok=True)
        if apply:
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            shutil.copy2(src, tmp)
            tmp.replace(dest)
        copied += 1

    mode = "APPLY" if apply else "DRY"
    print(f"[video_assets_pack] mode={mode} sync-to-run {_video_id(channel, video)}")
    print(f"- pack: {pack_dir}")
    print(f"- run : {run_dir}")
    print(f"- images_written: {copied}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    bootstrap(load_env=True)

    ap = argparse.ArgumentParser(description="Manage editor-agnostic episode asset packs (git-tracked).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("export", help="Export from run_dir (images + optional audio/srt) into asset pack.")
    sp.add_argument("--channel", default="", help="Channel code (e.g., CH01)")
    sp.add_argument("--video", default="", help="Video number (e.g., 220)")
    sp.add_argument("--run", default="", help="Explicit run_dir path (optional). If omitted, picks latest.")
    sp.add_argument("--include-audio", action="store_true", help="Also copy workspaces/audio/final wav/srt into pack.")
    sp.add_argument("--overwrite", action="store_true", help="Overwrite existing files in asset pack.")
    sp.add_argument("--write", action="store_true", help="Write files (default: dry-run).")
    sp.set_defaults(func=cmd_export)

    sp2 = sub.add_parser("ingest-images", help="Ingest externally created images (0001.png...) into asset pack.")
    sp2.add_argument("--channel", required=True, help="Channel code (e.g., CH01)")
    sp2.add_argument("--video", required=True, help="Video number (e.g., 220)")
    sp2.add_argument("--src", required=True, help="Source directory containing 0001.png... (png only).")
    sp2.add_argument("--overwrite", action="store_true", help="Overwrite existing files in asset pack.")
    sp2.add_argument("--write", action="store_true", help="Write files (default: dry-run).")
    sp2.set_defaults(func=cmd_ingest_images)

    sp3 = sub.add_parser("sync-to-run", help="Copy asset-pack images into run_dir/images (for CapCut swap-only etc).")
    sp3.add_argument("--run", required=True, help="Target run_dir")
    sp3.add_argument("--channel", default="", help="Channel code (optional if run_dir name has CHxx-NNN)")
    sp3.add_argument("--video", default="", help="Video number (optional if run_dir name has CHxx-NNN)")
    sp3.add_argument("--overwrite", action="store_true", help="Overwrite existing run_dir/images/*.png")
    sp3.add_argument("--apply", action="store_true", help="Apply changes (default: dry-run).")
    sp3.set_defaults(func=cmd_sync_to_run)

    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())


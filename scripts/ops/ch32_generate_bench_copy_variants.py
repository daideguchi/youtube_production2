#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ch32_generate_bench_copy_variants.py — generate CH32 thumbnail variants using benchmark copy (local yt-dlp OCR).

This script is designed for "layout sanity" and copy exploration:
- Uses operator-provided base images (e.g. workspaces/_scratch/ch32_1..4.png)
- Uses benchmark thumbnail wording extracted by yt-dlp analysis (thumbnail_insights)
- Writes variants under workspaces/thumbnails/assets/CH32/{NNN}/variants/{subdir}/...
- Publishes QC contactsheets into workspaces/thumbnails/assets/CH32/library/qc/

Safety:
- No external APIs / network calls.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from _bootstrap import bootstrap

bootstrap(load_env=True)

from factory_common import paths as fpaths  # noqa: E402


BENCHMARK_GENRE_DIR = "YouTubeベンチマーク（yt-dlp）"
DEFAULT_BENCHMARK_CHANNEL_ID = "UC9fcd5KCJfOeRsdRJZdSaGQ"  # 人生これから - ブッダの教え


def _normalize_channel(ch: str) -> str:
    return str(ch or "").strip().upper()


def _normalize_video(v: str) -> str:
    digits = "".join(c for c in str(v or "").strip() if c.isdigit())
    if not digits:
        raise ValueError(f"invalid video: {v}")
    return digits.zfill(3)


def _style_path_for_key(channel: str, style_key_or_path: str) -> Path:
    raw = str(style_key_or_path or "").strip()
    if not raw:
        raise ValueError("style key/path is empty")
    p = Path(raw).expanduser()
    if p.suffix.lower() == ".json":
        return p
    return fpaths.thumbnails_root() / "assets" / _normalize_channel(channel) / "library" / "style" / "variants" / f"{raw}.json"


def _benchmark_report_path(channel_id: str) -> Path:
    return fpaths.research_root() / BENCHMARK_GENRE_DIR / str(channel_id).strip() / "report.json"


def _clean_bench_lines(lines: Iterable[str]) -> List[str]:
    drop_keywords = ("ブッダ", "ブ・ダ", "教え")
    out: List[str] = []
    for ln in lines:
        s = str(ln or "").strip()
        if not s:
            continue
        # Drop small tag lines like "ブッダの教え" (keeps core hook wording).
        if any(k in s for k in drop_keywords) and len(s) <= 8:
            continue
        out.append(s)
    return out


@dataclass(frozen=True)
class BenchCopy:
    video_id: str
    lines: Tuple[str, ...]


def _load_bench_copies(
    *,
    bench_channel_id: str,
    max_copies: int,
    min_lines: int,
    max_lines: int,
    max_line_len: int,
    drop_placeholders: bool,
) -> List[BenchCopy]:
    report_path = _benchmark_report_path(bench_channel_id)
    if not report_path.exists():
        raise SystemExit(f"benchmark report not found: {report_path}")

    report = json.loads(report_path.read_text(encoding="utf-8"))
    insights = report.get("thumbnail_insights") or {}
    if not isinstance(insights, dict) or not insights:
        raise SystemExit(f"thumbnail_insights missing/empty in: {report_path}")

    copies: List[BenchCopy] = []
    for video_id, entry in insights.items():
        analysis = (entry or {}).get("analysis") or {}
        raw_text = analysis.get("thumbnail_text")
        if not raw_text:
            continue
        raw_lines = str(raw_text).replace("／", "/").splitlines()
        lines = _clean_bench_lines(raw_lines)
        if not lines:
            continue
        if drop_placeholders and any("○" in ln for ln in lines):
            continue
        if not (int(min_lines) <= len(lines) <= int(max_lines)):
            continue
        if any(len(ln) > int(max_line_len) for ln in lines):
            continue
        copies.append(BenchCopy(video_id=str(video_id), lines=tuple(lines)))

    copies.sort(key=lambda x: x.video_id)
    if max_copies > 0:
        copies = copies[: int(max_copies)]
    if not copies:
        raise SystemExit(
            f"no benchmark copies matched filters (min_lines={min_lines}, max_lines={max_lines}, max_line_len={max_line_len})"
        )
    return copies


def _font_path() -> Path:
    p = fpaths.thumbnails_root() / "assets" / "_fonts" / "NotoSansJP_wght.ttf"
    return p


def _base_images_from_workspace_scratch() -> List[Path]:
    root = fpaths.workspace_root() / "_scratch"
    return [root / f"ch32_{i}.png" for i in range(1, 5)]


def _run(cmd: Sequence[str]) -> None:
    subprocess.run(list(cmd), check=True)


def _write_manifest(out_dir: Path, *, channel: str, video: str, bench_channel_id: str, copies: List[BenchCopy], styles: List[str]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    lines: List[str] = []
    lines.append("# copy_bench_v2 (benchmark wording)")
    lines.append("")
    lines.append(f"target: {_normalize_channel(channel)}-{_normalize_video(video)}")
    lines.append(f"benchmark: {bench_channel_id}")
    lines.append("")
    lines.append("## styles")
    lines.append("")
    for s in styles:
        lines.append(f"- {s}")
    lines.append("")
    lines.append("## variants")
    lines.append("")
    for idx, item in enumerate(copies, start=1):
        copy_id = f"copy_{idx:02d}"
        pretty = " / ".join(item.lines)
        lines.append(f"- {copy_id}: {pretty}")
    path = out_dir / "manifest.md"
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Generate CH32 benchmark-copy thumbnail variants (local).")
    ap.add_argument("--channel", default="CH32")
    ap.add_argument("--video", default="037", help="target video number under thumbnails/assets/CH32/{NNN}/")
    ap.add_argument("--bench-channel-id", default=DEFAULT_BENCHMARK_CHANNEL_ID)
    ap.add_argument("--out-subdir", default="copy_bench_v2", help="variants subdir name")
    ap.add_argument(
        "--style",
        action="append",
        default=[],
        help="style key (in CH32/library/style/variants/) or JSON path (repeatable). default: face_safe_066_clean + face_safe_066_impact",
    )
    ap.add_argument("--max-copies", type=int, default=16)
    ap.add_argument("--min-lines", type=int, default=2)
    ap.add_argument("--max-lines", type=int, default=3)
    ap.add_argument("--max-line-len", type=int, default=12)
    ap.add_argument("--keep-placeholders", action="store_true", help="keep copies containing ○○ placeholders")
    ap.add_argument("--qc-tile-w", type=int, default=640)
    ap.add_argument("--qc-tile-h", type=int, default=360)
    ap.add_argument("--qc-cols", type=int, default=4)
    ap.add_argument("--qc-pad", type=int, default=12)
    ap.add_argument("--run", action="store_true", help="actually write images + QC")
    args = ap.parse_args(argv)

    channel = _normalize_channel(args.channel)
    video = _normalize_video(args.video)

    styles = [str(s).strip() for s in (args.style or []) if str(s).strip()]
    if not styles:
        styles = ["face_safe_066_clean", "face_safe_066_impact"]

    base_images = _base_images_from_workspace_scratch()
    missing = [p for p in base_images if not p.exists()]
    if missing:
        raise SystemExit(f"base images missing under {fpaths.workspace_root() / '_scratch'}: {', '.join(str(p) for p in missing)}")

    copies = _load_bench_copies(
        bench_channel_id=str(args.bench_channel_id).strip(),
        max_copies=int(args.max_copies),
        min_lines=int(args.min_lines),
        max_lines=int(args.max_lines),
        max_line_len=int(args.max_line_len),
        drop_placeholders=not bool(args.keep_placeholders),
    )

    assets_dir = fpaths.thumbnail_assets_dir(channel, video)
    variants_root = assets_dir / "variants" / str(args.out_subdir).strip()

    manifest_path = _write_manifest(
        variants_root,
        channel=channel,
        video=video,
        bench_channel_id=str(args.bench_channel_id).strip(),
        copies=copies,
        styles=styles,
    )
    print(f"[INFO] manifest: {manifest_path}")

    if not args.run:
        print("[INFO] dry-run only (use --run)")
        return 0

    font_path = _font_path()
    if not font_path.exists():
        raise SystemExit(f"font not found: {font_path}")

    apply_script = fpaths.repo_root() / "scripts" / "ops" / "ch32_apply_text_to_images.py"
    qc_script = fpaths.repo_root() / "scripts" / "ops" / "thumbnail_variant_qc.py"
    if not apply_script.exists():
        raise SystemExit(f"script not found: {apply_script}")
    if not qc_script.exists():
        raise SystemExit(f"script not found: {qc_script}")

    for style_key in styles:
        style_path = _style_path_for_key(channel, style_key)
        if not style_path.exists():
            raise SystemExit(f"style not found: {style_path}")
        for idx, item in enumerate(copies, start=1):
            copy_id = f"copy_{idx:02d}"
            out_dir = variants_root / str(style_key).replace(".json", "") / copy_id
            title = "\\\\n".join(item.lines)
            cmd = [
                sys.executable,
                str(apply_script),
                *[str(p) for p in base_images],
                "--run",
                "--force-channel",
                "--channel",
                channel,
                "--force-video",
                video,
                "--style",
                str(style_path),
                "--out-dir",
                str(out_dir),
                "--title",
                title,
                "--font-path",
                str(font_path),
                "--font-variation",
                "Black",
                "--align",
                "left",
            ]
            _run(cmd)

        include = f"variants/{args.out_subdir}/{str(style_key).replace('.json','')}/**/*.png"
        qc_out = fpaths.thumbnails_root() / "assets" / channel / "library" / "qc" / f"contactsheet_copy_bench_v2_{video}_{str(style_key).replace('.json','')}.png"
        qc_cmd = [
            sys.executable,
            str(qc_script),
            "--channel",
            channel,
            "--video",
            video,
            "--include",
            include,
            "--out",
            str(qc_out),
            "--tile-w",
            str(int(args.qc_tile_w)),
            "--tile-h",
            str(int(args.qc_tile_h)),
            "--cols",
            str(int(args.qc_cols)),
            "--pad",
            str(int(args.qc_pad)),
        ]
        _run(qc_cmd)
        print(f"[QC] wrote {qc_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

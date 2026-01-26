#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ch32_thumb_live.py — CH32 thumbnail "live preview" loop (AI対話→即反映→UI確認).

What this provides
------------------
1) A single style file you (or AI) tweak repeatedly:
   `workspaces/thumbnails/assets/CH32/library/style/live.json`

2) A local browser UI that auto-refreshes preview images:
   `http://127.0.0.1:<port>/live.html`

3) A watcher loop that rebuilds a small sample set (default 001-004) on changes:
   - style JSON
   - CH32 planning CSV

It rebuilds:
- `workspaces/thumbnails/assets/CH32/<NNN>/<NNN>_text_only.png`
- `workspaces/thumbnails/assets/CH32/<NNN>/00_thumb.png` (if `10_bg.*` exists)
- `workspaces/thumbnails/assets/CH32/library/qc/contactsheet.png`
- `workspaces/thumbnails/assets/CH32/library/qc/contactsheet_text_only_preview.png`

Notes
-----
- This is intentionally "minimal UI": you iterate by editing JSON (manually or via AI),
  while the browser shows the latest output instantly.
"""

from __future__ import annotations

import argparse
import http.server
import threading
import time
import webbrowser
from functools import partial
from pathlib import Path
from typing import Optional, Sequence

from _bootstrap import bootstrap

bootstrap(load_env=True)

from factory_common import paths as fpaths  # noqa: E402
from script_pipeline.thumbnails.io_utils import save_png_atomic  # noqa: E402

from PIL import Image, ImageDraw  # noqa: E402

import ch32_apply_text_to_images  # noqa: E402
import ch32_text_only_thumbs  # noqa: E402
from ch32_thumb_style import load_style  # noqa: E402


def _normalize_channel(ch: str) -> str:
    return str(ch or "").strip().upper()


def _normalize_video(v: str) -> str:
    digits = "".join(ch for ch in str(v or "").strip() if ch.isdigit())
    if not digits:
        raise ValueError(f"invalid video: {v}")
    return digits.zfill(3)


def _qc_dir(channel: str) -> Path:
    return fpaths.thumbnails_root() / "assets" / _normalize_channel(channel) / "library" / "qc"


def _write_live_html(*, qc_dir: Path, channel: str) -> None:
    qc_dir.mkdir(parents=True, exist_ok=True)
    html = f"""<!doctype html>
<html lang="ja">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{channel} thumbs live</title>
    <style>
      body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background:#111; color:#eee; margin:16px; }}
      .row {{ display:flex; gap:16px; flex-wrap:wrap; }}
      .card {{ background:#1b1b1b; border:1px solid #333; border-radius:10px; padding:12px; }}
      img {{ max-width: 100%; height:auto; border-radius:8px; background:#000; }}
      .hint {{ color:#aaa; font-size:12px; }}
      a {{ color:#9bd; }}
    </style>
  </head>
  <body>
    <h2>{channel} サムネ Live Preview</h2>
    <div class="hint">
      更新したら自動で再読み込みします（1秒間隔）｜
      編集: <code>workspaces/thumbnails/assets/{channel}/library/style/live.json</code>
    </div>
    <div class="row" style="margin-top:12px;">
      <div class="card">
        <div>00_thumb（サンプル）</div>
        <img id="imgA" src="contactsheet.png" />
      </div>
      <div class="card">
        <div>text_only（サンプル）</div>
        <img id="imgB" src="contactsheet_text_only_preview.png" />
      </div>
    </div>
    <script>
      function refresh() {{
        const ts = Date.now();
        const a = document.getElementById('imgA');
        const b = document.getElementById('imgB');
        if (a) a.src = 'contactsheet.png?ts=' + ts;
        if (b) b.src = 'contactsheet_text_only_preview.png?ts=' + ts;
      }}
      setInterval(refresh, 1000);
    </script>
  </body>
</html>
"""
    (qc_dir / "live.html").write_text(html, encoding="utf-8")


def _build_contactsheet(
    *,
    out_path: Path,
    items: list[tuple[str, Path]],
    cols: int,
    rows: int,
    mode: str,
) -> None:
    """
    mode:
      - "thumb": expects RGB/PNG thumbs
      - "text_only": expects RGBA overlays, composited on black for preview
    """
    cols = max(1, int(cols))
    rows = max(1, int(rows))
    w, h = 1920, 1080
    cell_w = w // cols
    cell_h = h // rows

    canvas = Image.new("RGB", (cell_w * cols, cell_h * rows), (0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    for idx, (vid, p) in enumerate(items[: cols * rows]):
        try:
            with Image.open(p) as im:
                im = im.convert("RGBA" if mode == "text_only" else "RGB")
        except Exception:
            continue

        if mode == "text_only":
            cell = Image.new("RGBA", (cell_w, cell_h), (0, 0, 0, 255))
            ov = im.resize((cell_w, cell_h), Image.Resampling.LANCZOS)
            cell.alpha_composite(ov)
            tile = cell.convert("RGB")
        else:
            tile = im.resize((cell_w, cell_h), Image.Resampling.LANCZOS).convert("RGB")

        x = (idx % cols) * cell_w
        y = (idx // cols) * cell_h
        canvas.paste(tile, (x, y))
        draw.text((x + 12, y + 10), str(vid), fill=(240, 240, 240))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    save_png_atomic(canvas, out_path, mode="final", verify=True)


def _build_sample_outputs(*, channel: str, videos: list[str], style_path: str, run: bool) -> None:
    argv = ["--channel", channel, "--videos", *videos, "--style", style_path]
    if run:
        argv.append("--run")
        argv.append("--compose")
    ch32_text_only_thumbs.main(argv)


def _build_scratch_previews(*, style_path: str, run: bool) -> None:
    scratch = fpaths.repo_root() / "workspaces" / "_scratch"
    imgs = [
        scratch / "ch32_1.png",
        scratch / "ch32_2.png",
        scratch / "ch32_3.png",
        scratch / "ch32_4.png",
    ]
    present = [str(p) for p in imgs if p.exists()]
    if not present:
        return
    argv = [*present, "--channel", "CH32", "--style", style_path]
    if run:
        argv.append("--run")
    ch32_apply_text_to_images.main(argv)


def _watch_mtime(paths: list[Path]) -> dict[str, float]:
    out: dict[str, float] = {}
    for p in paths:
        try:
            out[str(p)] = p.stat().st_mtime
        except FileNotFoundError:
            out[str(p)] = 0.0
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="CH32 thumbs live preview (watch style + rebuild + serve UI).")
    ap.add_argument("--channel", default="CH32")
    ap.add_argument("--style", default="", help="style JSON path (default: CH32 library/style/live.json)")
    ap.add_argument("--videos", nargs="*", default=None, help="sample videos (default: from style.preview.sample_videos)")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--interval", type=float, default=0.6, help="watch polling interval (seconds)")
    ap.add_argument("--no-open", action="store_true", help="do not open browser automatically")
    ap.add_argument("--no-scratch", action="store_true", help="do not rebuild workspaces/_scratch previews")
    ap.add_argument("--once", action="store_true", help="build once and exit (no watcher/server)")
    ap.add_argument("--run", action="store_true", help="actually write outputs (recommended)")
    args = ap.parse_args(argv)

    channel = _normalize_channel(args.channel)
    style, resolved_style = load_style(channel=channel, style_path=str(args.style or "").strip() or None)
    videos = [_normalize_video(v) for v in (args.videos or list(style.preview.sample_videos))]

    qc_dir = _qc_dir(channel)
    _write_live_html(qc_dir=qc_dir, channel=channel)

    planning_csv = fpaths.planning_root() / "channels" / f"{channel}.csv"
    watch_paths = [resolved_style, planning_csv]

    def build_all() -> None:
        print(f"[BUILD] {channel} videos={','.join(videos)} style={resolved_style}")
        _build_sample_outputs(channel=channel, videos=videos, style_path=str(resolved_style), run=bool(args.run))
        if not args.no_scratch:
            _build_scratch_previews(style_path=str(resolved_style), run=bool(args.run))

        # QC: composed thumbs (00_thumb) and text-only overlays.
        thumb_items: list[tuple[str, Path]] = []
        text_items: list[tuple[str, Path]] = []
        for v in videos:
            d = fpaths.thumbnail_assets_dir(channel, v)
            p_thumb = d / "00_thumb.png"
            if p_thumb.exists():
                thumb_items.append((v, p_thumb))
            p_text = d / f"{v}_text_only.png"
            if p_text.exists():
                text_items.append((v, p_text))

        if thumb_items:
            _build_contactsheet(
                out_path=qc_dir / "contactsheet.png",
                items=thumb_items,
                cols=int(style.preview.qc_cols),
                rows=int(style.preview.qc_rows),
                mode="thumb",
            )
        if text_items:
            _build_contactsheet(
                out_path=qc_dir / "contactsheet_text_only_preview.png",
                items=text_items,
                cols=int(style.preview.qc_cols),
                rows=int(style.preview.qc_rows),
                mode="text_only",
            )
        print("[BUILD] done")

    if args.once:
        build_all()
        print(f"[OK] once: open {qc_dir}/live.html")
        return 0

    # Start server.
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(qc_dir))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", int(args.port)), handler)

    def serve() -> None:
        httpd.serve_forever()

    t = threading.Thread(target=serve, daemon=True)
    t.start()

    url = f"http://127.0.0.1:{int(args.port)}/live.html"
    print(f"[SERVE] {url}")
    if not args.no_open:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    # Initial build.
    build_all()

    last = _watch_mtime(watch_paths)
    while True:
        time.sleep(max(0.15, float(args.interval)))
        cur = _watch_mtime(watch_paths)
        if cur != last:
            last = cur
            build_all()


if __name__ == "__main__":
    raise SystemExit(main())


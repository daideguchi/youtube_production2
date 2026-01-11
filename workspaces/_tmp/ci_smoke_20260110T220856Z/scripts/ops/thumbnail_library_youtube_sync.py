#!/usr/bin/env python3
"""
thumbnail_library_youtube_sync.py — YouTubeサムネ画像を「参照ライブラリ」に取り込む（buzz / benchmarks）。

目的:
  - 自チャンネルの「バズ（= top_by_views）」サムネを参照用に保存する
  - 競合（benchmarks.channels）のサムネも同様に保存する
  - 保存先は Thumbnail Compiler のチャンネル別ライブラリ配下（workspaces/thumbnails/assets/{CH}/library/）

前提入力（SoT）:
  - `scripts/ops/yt_dlp_benchmark_analyze.py` が生成した
    `workspaces/research/YouTubeベンチマーク（yt-dlp）/<UC...>/report.json`

出力:
  - buzz:
      workspaces/thumbnails/assets/{CH}/library/buzz/youtube/<UC...>__<handle>/{001...}_{video_id}_{maxres|hq}.jpg
      + index.json
  - benchmark:
      workspaces/thumbnails/assets/{CH}/library/benchmarks/youtube/<UC...>__<handle>/{001...}_{video_id}_{maxres|hq}.jpg
      + index.json

安全設計:
  - デフォルトは dry-run（書き込みなし）。`--apply` でのみ書き込み。
  - 既存ファイルは再DLしない（`--force` で上書き）。
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from _bootstrap import bootstrap

bootstrap(load_env=False)

from factory_common import paths as fpaths

try:
    from PIL import Image  # type: ignore
except Exception:  # pragma: no cover
    Image = None  # type: ignore[assignment]


YT_DLP_GENRE_DIR = "YouTubeベンチマーク（yt-dlp）"


def _safe_norm_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return int(value)
    s = _safe_norm_str(value)
    if not s:
        return None
    try:
        return int(float(s))
    except Exception:
        return None


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _ytimg_urls(video_id: str) -> Tuple[str, str]:
    return (
        f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
        f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
    )


def _download(url: str, *, out_path: Path, timeout_sec: int = 25) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    req = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            ),
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        },
    )
    with urlopen(req, timeout=timeout_sec) as resp:  # nosec B310
        data = resp.read()
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(out_path)


def _image_size(path: Path) -> Optional[Tuple[int, int]]:
    if Image is None:
        return None
    try:
        with Image.open(path) as img:
            return img.size
    except Exception:
        return None


def _is_reasonable_maxres(path: Path) -> bool:
    size = _image_size(path)
    if size is None:
        # Best-effort: at least not tiny
        try:
            return path.stat().st_size >= 50_000
        except Exception:
            return True
    w, h = size
    return w >= 1000 and h >= 500


def _download_with_fallback(
    *,
    video_id: str,
    out_dir: Path,
    base_name: str,
    force: bool,
    dry_run: bool,
) -> Tuple[str, str]:
    url_maxres, url_hq = _ytimg_urls(video_id)

    maxres_path = out_dir / f"{base_name}_maxres.jpg"
    hq_path = out_dir / f"{base_name}_hq.jpg"

    if not force:
        if maxres_path.exists():
            return (maxres_path.name, "maxres")
        if hq_path.exists():
            return (hq_path.name, "hq")

    if dry_run:
        # Prefer maxres in dry-run output.
        return (maxres_path.name, "maxres")

    try:
        _download(url_maxres, out_path=maxres_path)
        if _is_reasonable_maxres(maxres_path):
            return (maxres_path.name, "maxres")
    except (HTTPError, URLError):
        pass
    except Exception:
        pass

    # Fallback to hq
    _download(url_hq, out_path=hq_path)
    try:
        if maxres_path.exists():
            # Avoid keeping a low-res maxres placeholder.
            if not _is_reasonable_maxres(maxres_path):
                maxres_path.unlink(missing_ok=True)  # type: ignore[arg-type]
    except Exception:
        pass
    return (hq_path.name, "hq")


def _normalize_handle_for_dir(handle: Optional[str]) -> str:
    h = _safe_norm_str(handle) or "unknown"
    return h[1:] if h.startswith("@") else h


@dataclass(frozen=True)
class SyncTarget:
    tag: str  # buzz|benchmark
    report_path: Path


def _resolve_channel_info_dir(channel: str) -> Path:
    ch = str(channel).upper()
    root = fpaths.repo_root() / "packages" / "script_pipeline" / "channels"
    matches = sorted(root.glob(f"{ch}-*"))
    if not matches:
        raise SystemExit(f"channel info dir not found: {ch} (expected {root}/{ch}-*)")
    if len(matches) > 1:
        raise SystemExit(f"ambiguous channel info dirs for {ch}: {[m.name for m in matches]}")
    return matches[0]


def _collect_sync_targets(*, channel: str, include_self: bool, include_benchmarks: bool) -> List[SyncTarget]:
    ch_dir = _resolve_channel_info_dir(channel)
    info_path = ch_dir / "channel_info.json"
    info = _read_json(info_path)

    out: List[SyncTarget] = []

    if include_self:
        youtube = info.get("youtube") if isinstance(info.get("youtube"), dict) else {}
        ch_id = _safe_norm_str(youtube.get("channel_id"))
        if not ch_id:
            raise SystemExit(f"missing youtube.channel_id in {info_path}")
        report = fpaths.research_root() / YT_DLP_GENRE_DIR / ch_id / "report.json"
        out.append(SyncTarget(tag="buzz", report_path=report))

    if include_benchmarks:
        b = info.get("benchmarks") if isinstance(info.get("benchmarks"), dict) else {}
        channels = b.get("channels") if isinstance(b.get("channels"), list) else []
        for item in channels:
            if not isinstance(item, dict):
                continue
            url = _safe_norm_str(item.get("url"))
            handle = _safe_norm_str(item.get("handle"))
            ch_id = _safe_norm_str(item.get("channel_id"))
            # Prefer explicit channel_id if present; otherwise try url-hint in report locations.
            if ch_id:
                report = fpaths.research_root() / YT_DLP_GENRE_DIR / ch_id / "report.json"
                out.append(SyncTarget(tag="benchmark", report_path=report))
                continue
            # Best-effort: if the report already exists under a known UC folder, user can pass --report.
            raise SystemExit(
                "benchmarks.channels entry missing channel_id; run `yt_dlp_benchmark_analyze.py --url ... --apply` "
                f"and re-run with `--report` (url={url} handle={handle})."
            )

    return out


def _extract_videos_from_report(report: Dict[str, Any], *, target: str, limit: int) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    if target in {"top", "both"}:
        raw = report.get("top_by_views")
        if isinstance(raw, list):
            candidates.extend([x for x in raw if isinstance(x, dict)])
    if target in {"recent", "both"}:
        raw = report.get("recent")
        if isinstance(raw, list):
            candidates.extend([x for x in raw if isinstance(x, dict)])
    if target == "all":
        raw = report.get("videos")
        if isinstance(raw, list):
            candidates = [x for x in raw if isinstance(x, dict)]

    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for item in candidates:
        vid = _safe_norm_str(item.get("id"))
        if not vid or vid in seen:
            continue
        seen.add(vid)
        out.append(item)
        if limit and len(out) >= limit:
            break
    return out


def _sync_one_report(
    *,
    channel: str,
    report_path: Path,
    tag: str,
    target: str,
    limit: int,
    force: bool,
    apply: bool,
) -> Path:
    if not report_path.exists():
        raise SystemExit(
            f"report.json not found: {report_path}\n"
            "Run: python3 scripts/ops/yt_dlp_benchmark_analyze.py --url <youtube channel url> --apply"
        )

    report = _read_json(report_path)
    ch_meta = report.get("channel") if isinstance(report.get("channel"), dict) else {}
    playlist_channel_id = _safe_norm_str(ch_meta.get("playlist_channel_id")) or "UNKNOWN_CHANNEL"
    playlist_handle = _safe_norm_str(ch_meta.get("playlist_uploader_id"))
    playlist_name = _safe_norm_str(ch_meta.get("playlist_channel"))

    handle_slug = _normalize_handle_for_dir(playlist_handle)
    lib_root = fpaths.thumbnails_root() / "assets" / str(channel).upper() / "library"
    if tag == "buzz":
        out_root = lib_root / "buzz" / "youtube"
    else:
        out_root = lib_root / "benchmarks" / "youtube"
    out_dir = out_root / f"{playlist_channel_id}__{handle_slug}"

    videos = _extract_videos_from_report(report, target=target, limit=limit)

    index: List[Dict[str, Any]] = []
    for i, item in enumerate(videos, start=1):
        vid = _safe_norm_str(item.get("id"))
        title = _safe_norm_str(item.get("title")) or ""
        view_count = _safe_int(item.get("view_count"))
        if not vid:
            continue

        base_name = f"{i:03d}_{vid}"
        file_name, quality = _download_with_fallback(
            video_id=vid,
            out_dir=out_dir,
            base_name=base_name,
            force=force,
            dry_run=not apply,
        )
        url_maxres, url_hq = _ytimg_urls(vid)
        repo_root = fpaths.repo_root().resolve()
        try:
            source_report = str(report_path.resolve().relative_to(repo_root))
        except Exception:
            source_report = str(report_path)
        index.append(
            {
                "rank": i,
                "youtube_id": vid,
                "title": title,
                "view_count": view_count,
                "file": file_name,
                "quality": quality,
                "url_maxres": url_maxres,
                "url_hq": url_hq,
                "channel_id": playlist_channel_id,
                "channel_handle": playlist_handle,
                "channel_name": playlist_name,
                "tag": tag,
                "source_report": source_report,
                "target": target,
            }
        )

    if apply:
        out_dir.mkdir(parents=True, exist_ok=True)
        _write_json(out_dir / "index.json", index)

    return out_dir


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", required=True, help="Destination channel code (e.g. CH01).")
    parser.add_argument("--report", help="Optional: explicit report.json path (overrides --self/--benchmarks).")
    parser.add_argument("--self", action="store_true", help="Sync self-channel buzz thumbnails (top_by_views).")
    parser.add_argument("--benchmarks", action="store_true", help="Sync benchmark channels from channel_info.json.")
    parser.add_argument("--tag", choices=["buzz", "benchmark"], help="Tag override when using --report.")
    parser.add_argument("--target", choices=["top", "recent", "both", "all"], default="top")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--force", action="store_true", help="Re-download even if file already exists.")
    parser.add_argument("--apply", action="store_true", help="Write/download. Default is dry-run.")
    args = parser.parse_args()

    ch = str(args.channel).upper()
    target = str(args.target)
    limit = int(args.limit)
    force = bool(args.force)
    apply = bool(args.apply)

    if args.report:
        report_path = Path(args.report)
        tag = args.tag or "buzz"
        out_dir = _sync_one_report(
            channel=ch,
            report_path=report_path,
            tag=tag,
            target=target,
            limit=limit,
            force=force,
            apply=apply,
        )
        print(f"[ok] synced ({tag}) -> {out_dir}")
        return 0

    if not args.self and not args.benchmarks:
        raise SystemExit("Specify --self and/or --benchmarks, or pass --report.")

    targets = _collect_sync_targets(channel=ch, include_self=bool(args.self), include_benchmarks=bool(args.benchmarks))
    for t in targets:
        # Default behavior: self => buzz(top), benchmarks => benchmark(top)
        out_dir = _sync_one_report(
            channel=ch,
            report_path=t.report_path,
            tag=t.tag,
            target=target,
            limit=limit,
            force=force,
            apply=apply,
        )
        print(f"[ok] synced ({t.tag}) -> {out_dir}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

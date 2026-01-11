#!/usr/bin/env python3
"""
yt_dlp_benchmark_analyze.py — yt-dlpベースで競合チャンネルを徹底分析し、researchに整理レポートを生成する。

目的:
  - benchmarks.channels（競合/参考チャンネル）の直近動画メタを取得し、
    「タイトルの型 / 尺 / 再生数 / サムネURL」を、再利用しやすい形に整理する。
  - 生成物は workspaces/research に集約し、UI（/channel-settings 等）から参照できるようにする。

設計方針（事故防止）:
  - DLしない（メタデータ取得のみ）。
  - まずは安定な `--flat-playlist` を正とし、重い深掘りは後段に分離する。

入出力（SoT）:
  - 入力（競合定義SoT）:
      packages/script_pipeline/channels/CHxx-*/channel_info.json の benchmarks.channels
  - 生ログ（L3）:
      workspaces/logs/ops/yt_dlp/flat__<playlist_channel_id>__<timestamp>.jsonl
  - 整理レポート（research: 引用/再利用用）:
      workspaces/research/YouTubeベンチマーク（yt-dlp）/<playlist_channel_id>/report.{md,json}
  - 集約インデックス（UI向け）:
      workspaces/research/YouTubeベンチマーク（yt-dlp）/REPORTS.{md,json}

Usage:
  # 1件だけ（handle/URL）
  python3 scripts/ops/yt_dlp_benchmark_analyze.py --url "https://www.youtube.com/@HANDLE" --apply

  # 1チャンネルの benchmarks.channels をまとめて
  python3 scripts/ops/yt_dlp_benchmark_analyze.py --channel CH10 --apply

  # 全チャンネルの benchmarks.channels（重複除去）を一括
  python3 scripts/ops/yt_dlp_benchmark_analyze.py --all --apply
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import unquote, urlsplit, urlunsplit

from _bootstrap import bootstrap

bootstrap(load_env=False)

from factory_common.paths import logs_root, research_root, script_pkg_root

MANUAL_START = "<!-- MANUAL START -->"
MANUAL_END = "<!-- MANUAL END -->"
DEFAULT_MANUAL_PLACEHOLDER = "（ここは手動メモ。自動生成で保持されます）"

YT_DLP_GENRE_DIR = "YouTubeベンチマーク（yt-dlp）"
REPORTS_JSON_REL = f"{YT_DLP_GENRE_DIR}/REPORTS.json"
REPORTS_MD_REL = f"{YT_DLP_GENRE_DIR}/REPORTS.md"

CHANNEL_CODE_RE = re.compile(r"^CH\d{2}$", re.IGNORECASE)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _safe_norm_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_handle(value: Any) -> Optional[str]:
    raw = _safe_norm_str(value)
    if not raw:
        return None
    return raw if raw.startswith("@") else f"@{raw}"


def _normalize_channel_code(value: Any) -> Optional[str]:
    raw = _safe_norm_str(value)
    if not raw:
        return None
    s = raw.upper()
    if CHANNEL_CODE_RE.match(s):
        return s
    m = re.fullmatch(r"CH(\d+)", s)
    if m:
        return f"CH{int(m.group(1)):02d}"
    return None


def _normalize_url(value: Any) -> Optional[str]:
    raw = _safe_norm_str(value)
    if not raw:
        return None
    try:
        parts = urlsplit(raw)
    except Exception:
        return raw
    if not parts.scheme or not parts.netloc:
        return raw
    normalized_path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, normalized_path, "", ""))


def _extract_channel_id_from_url(url: str) -> Optional[str]:
    m = re.search(r"/channel/(UC[\w-]+)", url or "")
    return m.group(1) if m else None


def _extract_handle_from_url(url: str) -> Optional[str]:
    m = re.search(r"/@([^/?#]+)", url or "")
    if not m:
        return None
    return _normalize_handle(unquote(m.group(1)))


def _videos_url_from_handle(handle: str) -> str:
    h = _normalize_handle(handle) or handle
    return f"https://www.youtube.com/{h}/videos"


def _videos_url_from_channel_id(channel_id: str) -> str:
    return f"https://www.youtube.com/channel/{channel_id}/videos"


def _videos_url_from_any(url_or_handle: str) -> str:
    raw = (url_or_handle or "").strip()
    if not raw:
        raise ValueError("empty url/handle")
    if raw.startswith("@"):
        return _videos_url_from_handle(raw)
    url = _normalize_url(raw) or raw
    if url.endswith("/videos"):
        return url
    handle = _extract_handle_from_url(url)
    if handle:
        return _videos_url_from_handle(handle)
    channel_id = _extract_channel_id_from_url(url)
    if channel_id:
        return _videos_url_from_channel_id(channel_id)
    return url.rstrip("/") + "/videos"


def _percentile(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    if q <= 0:
        return float(min(values))
    if q >= 1:
        return float(max(values))
    xs = sorted(values)
    pos = (len(xs) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    if lo == hi:
        return float(xs[lo])
    frac = pos - lo
    return float(xs[lo] * (1 - frac) + xs[hi] * frac)


def _mean(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return float(sum(values) / len(values))


def _pearson_corr(xs: List[float], ys: List[float]) -> Optional[float]:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mx = _mean(xs)
    my = _mean(ys)
    if mx is None or my is None:
        return None
    num = 0.0
    sx = 0.0
    sy = 0.0
    for x, y in zip(xs, ys):
        dx = x - mx
        dy = y - my
        num += dx * dy
        sx += dx * dx
        sy += dy * dy
    if sx <= 0 or sy <= 0:
        return None
    return float(num / (sx**0.5 * sy**0.5))


def _extract_bracket_prefix(title: str) -> Optional[str]:
    t = (title or "").strip()
    if not t.startswith("【"):
        return None
    end = t.find("】")
    if end == -1:
        return None
    inner = t[1:end].strip()
    if not inner:
        return None
    if len(inner) > 24:
        return inner[:24] + "…"
    return inner


def _split_title_tokens(title: str) -> List[str]:
    raw = (title or "").strip()
    if not raw:
        return []
    parts = re.split(r"[\s\t\n\r、。・「」『』【】\[\]（）()!?！？…〜～—−:：;；/／|｜“”\"'’‘-]+", raw)
    out: List[str] = []
    for p in parts:
        token = p.strip()
        if len(token) < 2:
            continue
        if len(token) > 24:
            continue
        out.append(token)
    return out


def _safe_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _extract_manual_block(existing: str) -> str:
    if not existing:
        return ""
    start = existing.find(MANUAL_START)
    end = existing.find(MANUAL_END)
    if start == -1 or end == -1 or end <= start:
        return ""
    inner = existing[start + len(MANUAL_START) : end]
    return inner.strip("\n")


def _render_manual_block(existing: str, template: str) -> str:
    preserved = _extract_manual_block(existing).strip("\n")
    if not preserved.strip() or preserved.strip() == DEFAULT_MANUAL_PLACEHOLDER:
        body = template.strip("\n")
    else:
        body = preserved.strip("\n")
    return "\n".join([MANUAL_START, body, MANUAL_END])


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    _write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class TargetChannel:
    source: str  # url|handle|benchmarks
    channel_code: Optional[str]
    handle: Optional[str]
    url: Optional[str]

    def key(self) -> Tuple[str, str]:
    # stable dedupe key
        if self.handle:
            return ("handle", self.handle)
        if self.url:
            return ("url", self.url)
        return ("unknown", self.source)

    def videos_url(self) -> str:
        if self.handle:
            return _videos_url_from_handle(self.handle)
        if self.url:
            return _videos_url_from_any(self.url)
        raise ValueError("target has neither handle nor url")


def _iter_channel_info_paths() -> List[Path]:
    base = script_pkg_root() / "channels"
    if not base.exists():
        return []
    return sorted([p for p in base.glob("CH*-*/channel_info.json") if p.is_file()])


def _collect_targets_from_benchmarks(*, only_channel: Optional[str]) -> List[TargetChannel]:
    targets: List[TargetChannel] = []
    for info_path in _iter_channel_info_paths():
        try:
            payload = _read_json(info_path)
        except Exception:
            continue

        channel_code = _normalize_channel_code(payload.get("channel_id")) or _normalize_channel_code(
            info_path.parent.name.split("-", 1)[0]
        )
        if not channel_code:
            continue
        if only_channel and channel_code != only_channel:
            continue

        benchmarks = payload.get("benchmarks") if isinstance(payload.get("benchmarks"), dict) else {}
        channels = benchmarks.get("channels") if isinstance(benchmarks, dict) else []
        if not isinstance(channels, list):
            continue

        for item in channels:
            if not isinstance(item, dict):
                continue
            handle = _normalize_handle(item.get("handle"))
            url = _normalize_url(item.get("url"))
            if not handle and url:
                handle = _extract_handle_from_url(url)
            if not url and handle:
                url = _normalize_url(_videos_url_from_handle(handle).rsplit("/videos", 1)[0])
            if not handle and not url:
                continue
            targets.append(TargetChannel(source="benchmarks", channel_code=channel_code, handle=handle, url=url))
    return targets


def _dedupe_targets(targets: List[TargetChannel]) -> List[TargetChannel]:
    seen: set[Tuple[str, str]] = set()
    out: List[TargetChannel] = []
    for t in targets:
        key = t.key()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def _run_yt_dlp_flat_jsonl(*, videos_url: str, playlist_end: int) -> Iterable[Dict[str, Any]]:
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--playlist-end",
        str(int(playlist_end)),
        "--extractor-args",
        "youtube:lang=ja",
        "--dump-json",
        videos_url,
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert proc.stdout is not None
    assert proc.stderr is not None

    for raw in proc.stdout:
        line = raw.strip()
        if not line:
            continue
        if not line.startswith("{"):
            continue
        try:
            yield json.loads(line)
        except Exception:
            continue

    stderr = proc.stderr.read()
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"yt-dlp failed (rc={rc}): {stderr.strip()}")


def _run_yt_dlp_playlist_info(*, videos_url: str) -> Optional[Dict[str, Any]]:
    """
    Fetch playlist/channel-level metadata (including channel thumbnails) with yt-dlp.

    NOTE:
      - `--flat-playlist --dump-json` output does not include channel avatar thumbnails.
      - `-J --skip-download` does include `thumbnails` where `avatar_uncropped` may exist.
    """
    cmd = [
        "yt-dlp",
        "-J",
        "--skip-download",
        "--playlist-end",
        "1",
        "--extractor-args",
        "youtube:lang=ja",
        videos_url,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    raw = (proc.stdout or "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _pick_best_thumbnail_url(thumbnails: Any) -> Optional[str]:
    if not isinstance(thumbnails, list):
        return None
    best_url: Optional[str] = None
    best_score: int = -1
    for t in thumbnails:
        if not isinstance(t, dict):
            continue
        url = _safe_norm_str(t.get("url"))
        if not url:
            continue
        try:
            score = int(t.get("width") or 0) * int(t.get("height") or 0)
        except Exception:
            score = 0
        if score > best_score:
            best_score = score
            best_url = url
    return best_url


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return None


def _pick_channel_avatar_url(thumbnails: Any) -> Optional[str]:
    if not isinstance(thumbnails, list):
        return None

    avatar_candidates: List[Dict[str, Any]] = []
    for t in thumbnails:
        if not isinstance(t, dict):
            continue
        url = _safe_norm_str(t.get("url"))
        if not url:
            continue
        tid = str(t.get("id") or "").lower()
        if "avatar" in tid:
            avatar_candidates.append(t)

    if avatar_candidates:
        def _score(it: Dict[str, Any]) -> int:
            w = _safe_int(it.get("width")) or 0
            h = _safe_int(it.get("height")) or 0
            return int(w * h)

        best = max(avatar_candidates, key=_score)
        return _safe_norm_str(best.get("url"))

    squareish: List[Tuple[int, float, str]] = []
    for t in thumbnails:
        if not isinstance(t, dict):
            continue
        url = _safe_norm_str(t.get("url"))
        if not url:
            continue
        w = _safe_int(t.get("width"))
        h = _safe_int(t.get("height"))
        if not w or not h:
            continue
        ratio = w / h
        if 0.8 <= ratio <= 1.25:
            squareish.append((w * h, abs(1.0 - ratio), url))

    if squareish:
        squareish.sort(key=lambda x: (x[0], -x[1]), reverse=True)
        return squareish[0][2]

    return _pick_best_thumbnail_url(thumbnails)


def _extract_video_entry(item: Dict[str, Any]) -> Dict[str, Any]:
    title = _safe_norm_str(item.get("title")) or ""
    try:
        duration_f = float(item.get("duration")) if item.get("duration") is not None else None
    except Exception:
        duration_f = None
    view_i = _safe_int(item.get("view_count"))
    return {
        "id": _safe_norm_str(item.get("id")) or "",
        "title": title,
        "url": _safe_norm_str(item.get("webpage_url")) or _safe_norm_str(item.get("url")) or "",
        "duration_sec": duration_f,
        "view_count": view_i,
        "thumbnail_url": _pick_best_thumbnail_url(item.get("thumbnails")),
        "playlist_index": _safe_int(item.get("playlist_index")),
    }


def _analyze_entries(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    views: List[float] = []
    durations: List[float] = []
    title_lengths: List[float] = []
    bracket_prefix_counts: Dict[str, int] = {}
    token_counts: Dict[str, int] = {}

    starts_with_bracket = 0
    has_bar = 0
    has_quotes = 0
    has_question = 0
    has_exclamation = 0
    has_digits = 0
    has_age = 0

    view_for_corr: List[float] = []
    dur_for_corr: List[float] = []

    for e in entries:
        title = str(e.get("title") or "")
        title_lengths.append(float(len(title)))

        if title.startswith("【"):
            starts_with_bracket += 1
            prefix = _extract_bracket_prefix(title)
            if prefix:
                bracket_prefix_counts[prefix] = bracket_prefix_counts.get(prefix, 0) + 1

        if "｜" in title or "|" in title:
            has_bar += 1
        if "「" in title or "」" in title:
            has_quotes += 1
        if "？" in title or "?" in title:
            has_question += 1
        if "！" in title or "!" in title:
            has_exclamation += 1
        if re.search(r"[0-9０-９]", title):
            has_digits += 1
        if re.search(r"[0-9０-９]{2,3}代", title) or re.search(r"(\d{2,3})\s*代", title):
            has_age += 1

        for tok in _split_title_tokens(title):
            token_counts[tok] = token_counts.get(tok, 0) + 1

        v = e.get("view_count")
        if isinstance(v, int):
            views.append(float(v))
        d = e.get("duration_sec")
        if isinstance(d, (int, float)):
            durations.append(float(d))
        if isinstance(v, int) and isinstance(d, (int, float)):
            view_for_corr.append(float(v))
            dur_for_corr.append(float(d))

    bracket_sorted = sorted(bracket_prefix_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    token_sorted = sorted(token_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    corr = _pearson_corr(dur_for_corr, view_for_corr)

    n = len(entries)

    def _ratio(x: int) -> float:
        if n <= 0:
            return 0.0
        return float(x / n)

    return {
        "n": n,
        "views": {
            "min": min(views) if views else None,
            "p25": _percentile(views, 0.25),
            "median": _percentile(views, 0.5),
            "p75": _percentile(views, 0.75),
            "max": max(views) if views else None,
            "mean": _mean(views),
        },
        "durations_sec": {
            "min": min(durations) if durations else None,
            "p25": _percentile(durations, 0.25),
            "median": _percentile(durations, 0.5),
            "p75": _percentile(durations, 0.75),
            "max": max(durations) if durations else None,
            "mean": _mean(durations),
        },
        "title_len": {
            "min": min(title_lengths) if title_lengths else None,
            "p25": _percentile(title_lengths, 0.25),
            "median": _percentile(title_lengths, 0.5),
            "p75": _percentile(title_lengths, 0.75),
            "max": max(title_lengths) if title_lengths else None,
            "mean": _mean(title_lengths),
        },
        "title_patterns": {
            "starts_with_bracket_ratio": _ratio(starts_with_bracket),
            "has_bar_ratio": _ratio(has_bar),
            "has_quotes_ratio": _ratio(has_quotes),
            "has_question_ratio": _ratio(has_question),
            "has_exclamation_ratio": _ratio(has_exclamation),
            "has_digits_ratio": _ratio(has_digits),
            "has_age_ratio": _ratio(has_age),
            "top_bracket_prefixes": [{"prefix": k, "count": v} for k, v in bracket_sorted[:20]],
            "top_tokens": [{"token": k, "count": v} for k, v in token_sorted[:40]],
        },
        "corr_duration_vs_views": corr,
    }


def _duration_bucket_label(sec: float) -> str:
    minutes = sec / 60.0
    if minutes < 8:
        return "<8m"
    if minutes < 15:
        return "8-15m"
    if minutes < 25:
        return "15-25m"
    if minutes < 45:
        return "25-45m"
    return "45m+"


def _collect_duration_buckets(entries: List[Dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {"<8m": 0, "8-15m": 0, "15-25m": 0, "25-45m": 0, "45m+": 0}
    for e in entries:
        sec = e.get("duration_sec")
        if not isinstance(sec, (int, float)):
            continue
        out[_duration_bucket_label(float(sec))] += 1
    return out


def _format_duration(sec: Optional[float]) -> str:
    if sec is None:
        return "—"
    total = int(round(sec))
    mm = total // 60
    ss = total % 60
    return f"{mm:d}:{ss:02d}"


def _build_report(
    *,
    raw_items: List[Dict[str, Any]],
    source_url: str,
    playlist_end: int,
    fetched_at: str,
    channel_avatar_url: Optional[str],
) -> Dict[str, Any]:
    entries = [_extract_video_entry(x) for x in raw_items]
    entries = [e for e in entries if e.get("id") and e.get("title")]

    # playlist metadata (take first non-empty)
    playlist_channel_id = None
    playlist_uploader_id = None
    playlist_channel = None
    for it in raw_items:
        if not isinstance(it, dict):
            continue
        playlist_channel_id = playlist_channel_id or _safe_norm_str(it.get("playlist_channel_id"))
        playlist_uploader_id = playlist_uploader_id or _normalize_handle(it.get("playlist_uploader_id"))
        playlist_channel = playlist_channel or _safe_norm_str(it.get("playlist_channel")) or _safe_norm_str(it.get("playlist_uploader"))

    playlist_channel_id = playlist_channel_id or _extract_channel_id_from_url(source_url) or (playlist_uploader_id or "unknown")

    stats = _analyze_entries(entries)

    by_views = sorted(entries, key=lambda e: (_safe_int(e.get("view_count")) or -1), reverse=True)
    recent = sorted(entries, key=lambda e: (_safe_int(e.get("playlist_index")) or 10_000))

    channel_payload: Dict[str, Any] = {
        "playlist_channel_id": playlist_channel_id,
        "playlist_uploader_id": playlist_uploader_id,
        "playlist_channel": playlist_channel,
        "source_url": source_url,
    }
    if channel_avatar_url:
        channel_payload["avatar_url"] = channel_avatar_url

    return {
        "version": 1,
        "fetched_at": fetched_at,
        "playlist_end": int(playlist_end),
        "channel": channel_payload,
        "videos": recent,
        "stats": stats,
        "duration_buckets": _collect_duration_buckets(entries),
        "top_by_views": by_views[:10],
        "recent": recent[:10],
    }


def _render_report_md(*, existing_text: str, report: Dict[str, Any]) -> str:
    manual = _render_manual_block(
        existing_text,
        """## 勝ちパターン（手動）
- （このチャンネルの「刺さり方」を1〜5行で要約）

## 自チャンネルへの落とし込み（手動）
- タイトル型:
- 尺:
- 禁止/注意（断定/煽り/契約/危険表現など）:

## 次に深掘りする動画（手動）
- （URLを貼る / 何を見るか）
""",
    )

    ch = report.get("channel") if isinstance(report.get("channel"), dict) else {}
    stats = report.get("stats") if isinstance(report.get("stats"), dict) else {}
    views = stats.get("views") if isinstance(stats.get("views"), dict) else {}
    durs = stats.get("durations_sec") if isinstance(stats.get("durations_sec"), dict) else {}
    tl = stats.get("title_len") if isinstance(stats.get("title_len"), dict) else {}
    patterns = stats.get("title_patterns") if isinstance(stats.get("title_patterns"), dict) else {}

    lines: List[str] = []
    header = ch.get("playlist_channel") or ch.get("playlist_uploader_id") or ch.get("playlist_channel_id") or "unknown"
    lines.append(f"# yt-dlp ベンチマークレポート — {header}")
    lines.append("")
    lines.append(manual)
    lines.append("")
    lines.append("## 概要")
    lines.append("")
    lines.append(f"- fetched_at: `{report.get('fetched_at')}`")
    lines.append(f"- source_url: `{ch.get('source_url')}`")
    lines.append(f"- playlist_channel_id: `{ch.get('playlist_channel_id')}`")
    lines.append(f"- playlist_uploader_id: `{ch.get('playlist_uploader_id')}`")
    lines.append(f"- videos: `{stats.get('n')}` / playlist_end: `{report.get('playlist_end')}`")
    lines.append("")

    lines.append("## サマリ統計")
    lines.append("")
    lines.append(f"- views median/p75/max: `{views.get('median')}` / `{views.get('p75')}` / `{views.get('max')}`")
    lines.append(
        f"- duration median/p75/max: `{_format_duration(durs.get('median'))}` / `{_format_duration(durs.get('p75'))}` / `{_format_duration(durs.get('max'))}`"
    )
    lines.append(f"- title_len median/p75/max: `{tl.get('median')}` / `{tl.get('p75')}` / `{tl.get('max')}`")
    lines.append(f"- corr(duration, views): `{stats.get('corr_duration_vs_views')}`")
    lines.append("")

    lines.append("## タイトルの型（比率）")
    lines.append("")
    lines.append(f"- starts_with_【】: `{patterns.get('starts_with_bracket_ratio')}`")
    lines.append(f"- has_｜: `{patterns.get('has_bar_ratio')}`")
    lines.append(f"- has_「」: `{patterns.get('has_quotes_ratio')}`")
    lines.append(f"- has_？: `{patterns.get('has_question_ratio')}`")
    lines.append(f"- has_！: `{patterns.get('has_exclamation_ratio')}`")
    lines.append(f"- has_digits: `{patterns.get('has_digits_ratio')}`")
    lines.append(f"- has_age: `{patterns.get('has_age_ratio')}`")
    lines.append("")

    lines.append("## 【カテゴリ】上位")
    lines.append("")
    top_prefixes = patterns.get("top_bracket_prefixes") if isinstance(patterns.get("top_bracket_prefixes"), list) else []
    if not top_prefixes:
        lines.append("- （なし）")
    else:
        for it in top_prefixes[:12]:
            if not isinstance(it, dict):
                continue
            lines.append(f"- `{it.get('prefix')}`: {it.get('count')}")
    lines.append("")

    lines.append("## タイトル断片（頻出）")
    lines.append("")
    top_tokens = patterns.get("top_tokens") if isinstance(patterns.get("top_tokens"), list) else []
    if not top_tokens:
        lines.append("- （なし）")
    else:
        for it in top_tokens[:20]:
            if not isinstance(it, dict):
                continue
            lines.append(f"- `{it.get('token')}`: {it.get('count')}")
    lines.append("")

    lines.append("## 尺の分布（件数）")
    lines.append("")
    buckets = report.get("duration_buckets") if isinstance(report.get("duration_buckets"), dict) else {}
    if not buckets:
        lines.append("- （なし）")
    else:
        for key in ["<8m", "8-15m", "15-25m", "25-45m", "45m+"]:
            lines.append(f"- {key}: {int(buckets.get(key) or 0)}")
    lines.append("")

    def _render_video_list(title: str, items: Any) -> None:
        lines.append(f"## {title}")
        lines.append("")
        if not isinstance(items, list) or not items:
            lines.append("- （なし）")
            lines.append("")
            return
        for it in items:
            if not isinstance(it, dict):
                continue
            vc = it.get("view_count")
            dur = it.get("duration_sec")
            t = str(it.get("title") or "").strip()
            u = str(it.get("url") or "").strip()
            lines.append(f"- `{vc}` / `{_format_duration(dur)}` — {t}")
            if u:
                lines.append(f"  - {u}")
            if it.get("thumbnail_url"):
                lines.append(f"  - thumb: {it.get('thumbnail_url')}")
        lines.append("")

    _render_video_list("再生数 上位", report.get("top_by_views"))
    _render_video_list("直近（新しい順）", report.get("recent"))

    return "\n".join(lines)


def _render_reports_index_md(*, existing_text: str, index_payload: Dict[str, Any]) -> str:
    manual = _render_manual_block(
        existing_text,
        """## 運用メモ（手動）
- 生成: `python3 scripts/ops/yt_dlp_benchmark_analyze.py --all --apply`
- UI: Channel Settings → ベンチマーク → 競合チャンネル → yt-dlpレポート
""",
    )

    entries = index_payload.get("entries") if isinstance(index_payload.get("entries"), list) else []
    lines: List[str] = []
    lines.append("# YouTubeベンチマーク（yt-dlp）— レポート集約")
    lines.append("")
    lines.append(manual)
    lines.append("")
    lines.append("## 一覧")
    lines.append("")

    if not entries:
        lines.append("- （なし）")
        return "\n".join(lines)

    def _sort_key(it: Dict[str, Any]) -> Tuple[int, str]:
        stats = it.get("stats") if isinstance(it.get("stats"), dict) else {}
        median = stats.get("view_count_median")
        try:
            m = int(median) if median is not None else -1
        except Exception:
            m = -1
        return (-m, str(it.get("playlist_channel_id") or ""))

    for it in sorted([x for x in entries if isinstance(x, dict)], key=_sort_key):
        pid = str(it.get("playlist_channel_id") or "")
        handle = str(it.get("playlist_uploader_id") or "")
        name = str(it.get("playlist_channel") or "") or handle or pid
        fetched_at = str(it.get("fetched_at") or "")
        md_path = str(it.get("report_md_path") or "")
        stats = it.get("stats") if isinstance(it.get("stats"), dict) else {}
        median_views = stats.get("view_count_median")
        median_dur = stats.get("duration_median_sec")
        lines.append(f"- **{name}** `{handle}`")
        lines.append(f"  - id: `{pid}` / fetched_at: `{fetched_at}`")
        if md_path:
            lines.append(f"  - report: `{md_path}`")
        if median_views is not None or median_dur is not None:
            lines.append(f"  - median views: `{median_views}` / median duration: `{_format_duration(median_dur)}`")
    return "\n".join(lines)


def _resolve_report_paths(playlist_channel_id: str) -> Tuple[Path, Path, str, str]:
    out_dir = research_root() / YT_DLP_GENRE_DIR / playlist_channel_id
    report_md_path = out_dir / "report.md"
    report_json_path = out_dir / "report.json"
    rel_md = f"{YT_DLP_GENRE_DIR}/{playlist_channel_id}/report.md"
    rel_json = f"{YT_DLP_GENRE_DIR}/{playlist_channel_id}/report.json"
    return report_md_path, report_json_path, rel_md, rel_json


def _load_reports_index_json() -> Dict[str, Any]:
    index_path = research_root() / REPORTS_JSON_REL
    if not index_path.exists():
        return {"version": 1, "generated_at": None, "entries": []}
    try:
        return json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "generated_at": None, "entries": []}


def _coerce_index_by_id(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    entries = payload.get("entries") if isinstance(payload.get("entries"), list) else []
    by_id: Dict[str, Dict[str, Any]] = {}
    for it in entries:
        if not isinstance(it, dict):
            continue
        pid = str(it.get("playlist_channel_id") or "").strip()
        if pid:
            by_id[pid] = it
    return by_id


def _make_index_entry(*, report: Dict[str, Any], rel_md: str, rel_json: str) -> Dict[str, Any]:
    ch = report.get("channel") if isinstance(report.get("channel"), dict) else {}
    stats = report.get("stats") if isinstance(report.get("stats"), dict) else {}
    views = stats.get("views") if isinstance(stats.get("views"), dict) else {}
    durs = stats.get("durations_sec") if isinstance(stats.get("durations_sec"), dict) else {}
    patterns = stats.get("title_patterns") if isinstance(stats.get("title_patterns"), dict) else {}
    top_prefixes = patterns.get("top_bracket_prefixes") if isinstance(patterns.get("top_bracket_prefixes"), list) else []
    top_prefix = top_prefixes[0].get("prefix") if top_prefixes and isinstance(top_prefixes[0], dict) else None
    top_by_views = report.get("top_by_views") if isinstance(report.get("top_by_views"), list) else []
    top_video = top_by_views[0] if top_by_views and isinstance(top_by_views[0], dict) else None
    top_video_payload = None
    if top_video:
        top_video_payload = {
            "id": top_video.get("id"),
            "title": top_video.get("title"),
            "url": top_video.get("url"),
            "duration_sec": top_video.get("duration_sec"),
            "view_count": top_video.get("view_count"),
            "thumbnail_url": top_video.get("thumbnail_url"),
            "playlist_index": top_video.get("playlist_index"),
        }

    pid = str(ch.get("playlist_channel_id") or "").strip()
    return {
        "playlist_channel_id": pid,
        "playlist_uploader_id": ch.get("playlist_uploader_id"),
        "playlist_channel": ch.get("playlist_channel"),
        "channel_avatar_url": ch.get("avatar_url"),
        "source_url": ch.get("source_url"),
        "fetched_at": report.get("fetched_at"),
        "playlist_end": report.get("playlist_end"),
        "video_count": stats.get("n"),
        "report_md_path": rel_md,
        "report_json_path": rel_json,
        "top_video": top_video_payload,
        "stats": {
            "view_count_median": views.get("median"),
            "view_count_p75": views.get("p75"),
            "duration_median_sec": durs.get("median"),
            "title_starts_with_bracket_ratio": patterns.get("starts_with_bracket_ratio"),
            "top_bracket_prefix": top_prefix,
        },
    }


def _build_reports_index_payload(by_id: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "version": 1,
        "generated_at": _utc_now_iso(),
        "entries": list(by_id.values()),
    }


def _rebuild_reports_index_from_disk() -> Dict[str, Any]:
    genre_dir = research_root() / YT_DLP_GENRE_DIR
    by_id: Dict[str, Dict[str, Any]] = {}
    for report_json_path in sorted(genre_dir.glob("*/report.json"), key=lambda p: p.as_posix()):
        try:
            report = json.loads(report_json_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(report, dict):
            continue
        folder = report_json_path.parent.name
        rel_md = f"{YT_DLP_GENRE_DIR}/{folder}/report.md"
        rel_json = f"{YT_DLP_GENRE_DIR}/{folder}/report.json"
        entry = _make_index_entry(report=report, rel_md=rel_md, rel_json=rel_json)
        pid = str(entry.get("playlist_channel_id") or "").strip()
        if pid:
            by_id[pid] = entry
    return _build_reports_index_payload(by_id)


def _resolve_videos_url_for_existing_report(report: Dict[str, Any], report_json_path: Path) -> Optional[str]:
    ch = report.get("channel") if isinstance(report.get("channel"), dict) else {}
    source_url = _safe_norm_str(ch.get("source_url"))
    if source_url:
        return source_url

    handle = _normalize_handle(ch.get("playlist_uploader_id"))
    if handle:
        return _videos_url_from_handle(handle)

    channel_id = _safe_norm_str(ch.get("playlist_channel_id")) or report_json_path.parent.name
    if channel_id and channel_id.startswith("UC"):
        return _videos_url_from_channel_id(channel_id)

    return None


def _update_report_channel_avatars_from_disk(*, apply: bool) -> List[str]:
    genre_dir = research_root() / YT_DLP_GENRE_DIR
    updated: List[str] = []

    for report_json_path in sorted(genre_dir.glob("*/report.json"), key=lambda p: p.as_posix()):
        try:
            report = _read_json(report_json_path)
        except Exception:
            continue
        if not isinstance(report, dict):
            continue

        videos_url = _resolve_videos_url_for_existing_report(report, report_json_path)
        if not videos_url:
            continue

        playlist_info = _run_yt_dlp_playlist_info(videos_url=videos_url)
        avatar_url = _pick_channel_avatar_url(playlist_info.get("thumbnails") if playlist_info else None)
        if not avatar_url:
            continue

        ch = report.get("channel") if isinstance(report.get("channel"), dict) else {}
        current_avatar = _safe_norm_str(ch.get("avatar_url"))
        if current_avatar == avatar_url:
            continue

        next_channel: Dict[str, Any] = dict(ch) if isinstance(ch, dict) else {}
        next_channel["avatar_url"] = avatar_url
        report["channel"] = next_channel

        if apply:
            _write_json(report_json_path, report)

        updated.append(str(report_json_path.relative_to(research_root())))

    return updated


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", action="append", default=[], help="YouTube channel URL (handle/channel).")
    parser.add_argument("--handle", action="append", default=[], help="YouTube handle (e.g. @example).")
    parser.add_argument("--channel", help="Analyze benchmarks.channels for a specific CHxx.")
    parser.add_argument("--all", action="store_true", help="Analyze all unique benchmarks.channels across channels.")
    parser.add_argument("--playlist-end", type=int, default=80, help="How many recent videos to inspect (default: 80).")
    parser.add_argument(
        "--rebuild-index",
        action="store_true",
        help="Rebuild REPORTS.{json,md} from existing report.json files (no yt-dlp).",
    )
    parser.add_argument(
        "--update-avatars",
        action="store_true",
        help="Update channel avatar_url in existing report.json files (no flat playlist).",
    )
    parser.add_argument("--apply", action="store_true", help="Write outputs.")
    args = parser.parse_args()

    if args.rebuild_index:
        if not args.apply:
            print("[dry-run] will rebuild:")
            print(f"- workspaces/research/{REPORTS_JSON_REL}")
            print(f"- workspaces/research/{REPORTS_MD_REL}")
            print("")
            print("Run with --apply to write files.")
            return 0

        payload = _rebuild_reports_index_from_disk()
        index_md_existing = _safe_text(research_root() / REPORTS_MD_REL)
        _write_json(research_root() / REPORTS_JSON_REL, payload)
        _write_text(research_root() / REPORTS_MD_REL, _render_reports_index_md(existing_text=index_md_existing, index_payload=payload))
        print("updated:")
        print(f"- workspaces/research/{REPORTS_JSON_REL}")
        print(f"- workspaces/research/{REPORTS_MD_REL}")
        return 0

    if args.update_avatars:
        updated = _update_report_channel_avatars_from_disk(apply=args.apply)
        if not args.apply:
            if not updated:
                print("[dry-run] no avatar updates needed")
                return 0
            print("[dry-run] will update report.json:")
            for path in updated:
                print(f"- workspaces/research/{path}")
            print("")
            print("Run with --apply to write files, then run --rebuild-index to refresh REPORTS.{json,md}.")
            return 0

        index_md_existing = _safe_text(research_root() / REPORTS_MD_REL)
        index_payload = _rebuild_reports_index_from_disk()
        _write_json(research_root() / REPORTS_JSON_REL, index_payload)
        _write_text(research_root() / REPORTS_MD_REL, _render_reports_index_md(existing_text=index_md_existing, index_payload=index_payload))

        if not updated:
            print("no avatar updates needed")
        else:
            print("updated:")
            for path in updated:
                print(f"- workspaces/research/{path}")
            print(f"- workspaces/research/{REPORTS_JSON_REL}")
            print(f"- workspaces/research/{REPORTS_MD_REL}")
        return 0

    only_channel = _normalize_channel_code(args.channel) if args.channel else None
    if args.channel and not only_channel:
        raise SystemExit("invalid --channel (expected CHxx)")

    targets: List[TargetChannel] = []
    for u in args.url or []:
        url = _normalize_url(u)
        handle = _extract_handle_from_url(url or "") if url else None
        targets.append(TargetChannel(source="url", channel_code=None, handle=handle, url=url))
    for h in args.handle or []:
        handle = _normalize_handle(h)
        if not handle:
            continue
        targets.append(
            TargetChannel(
                source="handle",
                channel_code=None,
                handle=handle,
                url=_normalize_url(_videos_url_from_handle(handle).rsplit("/videos", 1)[0]),
            )
        )

    if args.all or only_channel:
        targets.extend(_collect_targets_from_benchmarks(only_channel=only_channel if not args.all else None))

    targets = _dedupe_targets(targets)
    if not targets:
        raise SystemExit("no targets (use --url/--handle/--channel/--all)")

    if not args.apply:
        print("[dry-run] targets:")
        for t in targets:
            print(f"- {t.videos_url()} ({t.handle or t.url or '-'})")
        print("\nRun with --apply to write reports.")
        return 0

    ts = _utc_now_compact()
    fetched_at = _utc_now_iso()

    log_dir = logs_root() / "ops" / "yt_dlp"
    index_md_existing = _safe_text(research_root() / REPORTS_MD_REL)

    updated: List[str] = []
    generated_any = False

    for t in targets:
        videos_url = t.videos_url()
        raw_items: List[Dict[str, Any]] = []
        try:
            for item in _run_yt_dlp_flat_jsonl(videos_url=videos_url, playlist_end=args.playlist_end):
                raw_items.append(item)
        except Exception as exc:
            print(f"[WARN] skip {videos_url}: {exc}")
            continue

        playlist_info = _run_yt_dlp_playlist_info(videos_url=videos_url)
        avatar_url = _pick_channel_avatar_url(playlist_info.get("thumbnails") if playlist_info else None)
        report = _build_report(
            raw_items=raw_items,
            source_url=videos_url,
            playlist_end=args.playlist_end,
            fetched_at=fetched_at,
            channel_avatar_url=avatar_url,
        )
        ch = report.get("channel") if isinstance(report.get("channel"), dict) else {}
        playlist_channel_id = str(ch.get("playlist_channel_id") or "").strip() or "unknown"

        raw_log_path = log_dir / f"flat__{playlist_channel_id}__{ts}.jsonl"
        raw_log_path.parent.mkdir(parents=True, exist_ok=True)
        with raw_log_path.open("w", encoding="utf-8") as f:
            for it in raw_items:
                f.write(json.dumps(it, ensure_ascii=False) + "\n")

        report_md_path, report_json_path, rel_md, rel_json = _resolve_report_paths(playlist_channel_id)
        md = _render_report_md(existing_text=_safe_text(report_md_path), report=report)
        _write_text(report_md_path, md)

        if report_json_path.exists():
            try:
                existing_report = _read_json(report_json_path)
            except Exception:
                existing_report = None
            if isinstance(existing_report, dict):
                for key in ("thumbnail_insights", "thumbnail_summary"):
                    if key in existing_report and key not in report:
                        report[key] = existing_report[key]

        _write_json(report_json_path, report)

        updated.append(rel_md)
        updated.append(rel_json)
        generated_any = True

    if not generated_any:
        print("no reports generated")
        return 2

    index_payload = _rebuild_reports_index_from_disk()
    _write_json(research_root() / REPORTS_JSON_REL, index_payload)
    _write_text(research_root() / REPORTS_MD_REL, _render_reports_index_md(existing_text=index_md_existing, index_payload=index_payload))

    print("updated:")
    for p in sorted(set(updated)):
        print(f"- workspaces/research/{p}")
    print(f"- workspaces/research/{REPORTS_JSON_REL}")
    print(f"- workspaces/research/{REPORTS_MD_REL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
yt_dlp_thumbnail_analyze.py — yt-dlpベンチマーク（research）に「サムネ言語化/分析」を付与する。

前提:
  - `scripts/ops/yt_dlp_benchmark_analyze.py` が生成した
    `workspaces/research/YouTubeベンチマーク（yt-dlp）/*/report.json` を入力（SoT）として扱う。
  - 本スクリプトはデフォルトで「オフライン簡易解析」（外部LLM/APIを呼ばない）で
    サムネの内容を「言語化」して JSON に保存する。
  - 追加で詳細な解析が必要な場合のみ `--use-llm` で LLM(Vision) を使う。

設計方針（事故防止）:
  - 既存 report.json の構造は壊さず、追加キーとして `thumbnail_insights` / `thumbnail_summary` を付与する。
  - 1チャンネルにつき対象動画数は絞る（デフォルト: top_by_views + recent のユニオン最大 20件）。
  - 既に分析済みの動画は再実行しない（--force で上書き）。

Usage:
  # 1チャンネルだけ（デフォルト: オフライン簡易解析）
  python3 scripts/ops/yt_dlp_thumbnail_analyze.py --channel-id UCOmPg-Ncs7XA5Jt_JBUJDmg --apply

  # LLM(Vision) を使って解析（明示指定）
  python3 scripts/ops/yt_dlp_thumbnail_analyze.py --channel-id UCOmPg-Ncs7XA5Jt_JBUJDmg --use-llm --apply

  # 全チャンネル
  python3 scripts/ops/yt_dlp_thumbnail_analyze.py --all --apply
"""

from __future__ import annotations

import argparse
import io
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.request import Request, urlopen

from _bootstrap import bootstrap

bootstrap(load_env=False)

from factory_common.paths import research_root

YT_DLP_GENRE_DIR = "YouTubeベンチマーク（yt-dlp）"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ytimg_hqdefault_url(video_id: str) -> str:
    """
    Return a stable thumbnail URL for a YouTube video.

    Some `yt-dlp` extracted thumbnail URLs (e.g. i9.ytimg.com custom variants)
    can intermittently 404 from the LLM provider fetch environment. Using the
    canonical hqdefault URL is more reliable.
    """
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def _extract_bracket_prefix(title: str) -> Optional[str]:
    t = (title or "").strip()
    if not t.startswith("【"):
        return None
    end = t.find("】")
    if end <= 1:
        return None
    inner = t[1:end].strip()
    return inner or None


def _infer_hook_type_from_title(title: str) -> str:
    t = (title or "").strip()
    # Priority: warning/expose > question/compare/reversal > empathy > assertion > other
    warning = ["危険", "超危険", "絶対", "放置", "助けてはいけない", "許してはいけない", "不幸", "人生が不幸", "損", "舐められ", "見下"]
    expose = ["正体", "真実", "裏", "実態", "本当", "闇", "知らない", "９割", "9割", "99%"]
    question = ["なぜ", "理由", "どうして", "？", "?"]
    compare = ["VS", "vs", "比較", "違い", "どっち"]
    reversal = ["実は", "逆転", "誤解", "真逆", "勘違い"]
    empathy = ["つら", "苦し", "不安", "悩", "孤独", "クヨクヨ", "心配"]
    assertive = ["結論", "最強", "無敵", "激変", "必ず"]

    if any(k in t for k in warning):
        return "警告"
    if any(k in t for k in expose):
        return "暴露"
    if any(k in t for k in question):
        return "質問"
    if any(k in t for k in compare):
        return "比較"
    if any(k in t for k in reversal):
        return "逆転"
    if any(k in t for k in empathy):
        return "共感"
    if any(k in t for k in assertive):
        return "断言"
    return "その他"


def _download_image_rgb(url: str, *, timeout_sec: float = 12.0) -> Optional["Image.Image"]:
    try:
        from PIL import Image  # type: ignore
    except Exception:
        return None

    u = (url or "").strip()
    if not u:
        return None

    try:
        req = Request(u, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=float(timeout_sec)) as resp:
            data = resp.read()
        with Image.open(io.BytesIO(data)) as im:
            return im.convert("RGB")
    except Exception:
        return None


def _edge_strength(im_rgb: "Image.Image") -> float:
    try:
        from PIL import ImageFilter, ImageStat  # type: ignore
    except Exception:
        return 0.0
    try:
        edges = im_rgb.convert("L").filter(ImageFilter.FIND_EDGES)
        return float((ImageStat.Stat(edges).mean or [0.0])[0])
    except Exception:
        return 0.0


def _brightness_stats(im_rgb: "Image.Image") -> tuple[float, float]:
    try:
        from PIL import ImageStat  # type: ignore
    except Exception:
        return (0.0, 0.0)
    try:
        st = ImageStat.Stat(im_rgb.convert("L"))
        mean = float((st.mean or [0.0])[0])
        std = float((st.stddev or [0.0])[0])
        return (mean, std)
    except Exception:
        return (0.0, 0.0)


def _color_ratios(im_rgb: "Image.Image") -> Dict[str, float]:
    # Fast heuristic: downsample and count coarse categories.
    try:
        small = im_rgb.resize((240, 135)).convert("RGB")
        pixels = list(small.getdata())
    except Exception:
        return {}
    total = float(len(pixels) or 1)

    def is_dark(r: int, g: int, b: int) -> bool:
        return r < 60 and g < 60 and b < 60

    def is_white(r: int, g: int, b: int) -> bool:
        return r > 210 and g > 210 and b > 210

    def is_red(r: int, g: int, b: int) -> bool:
        return r > 160 and g < 120 and b < 120

    def is_gold(r: int, g: int, b: int) -> bool:
        return r > 180 and g > 140 and b < 140

    dark = sum(1 for r, g, b in pixels if is_dark(r, g, b)) / total
    white = sum(1 for r, g, b in pixels if is_white(r, g, b)) / total
    red = sum(1 for r, g, b in pixels if is_red(r, g, b)) / total
    gold = sum(1 for r, g, b in pixels if is_gold(r, g, b)) / total
    return {"dark": float(dark), "white": float(white), "red": float(red), "gold": float(gold)}


def _analyze_thumbnail_offline(
    *,
    thumbnail_url: str,
    title: str,
    view_count: Optional[int],
    duration_sec: Optional[float],
) -> Dict[str, Any]:
    """
    Offline heuristic analyzer (no paid Vision LLM).

    This intentionally does NOT attempt Japanese OCR by default, because tesseract language packs
    may not be installed on all operator machines. `thumbnail_text` is set to null.
    """
    im = _download_image_rgb(thumbnail_url)
    if im is None:
        raise RuntimeError("offline_download_failed")

    w, h = im.size
    top_h = max(1, int(h * 0.30))
    left_w = max(1, int(w * 0.55))
    bottom = im.crop((0, top_h, w, h))
    top = im.crop((0, 0, w, top_h))
    left = im.crop((0, 0, left_w, h))
    right = im.crop((int(w * 0.45), 0, w, h))

    e_top = _edge_strength(top)
    e_bottom = _edge_strength(bottom)
    e_left = _edge_strength(left)
    e_right = _edge_strength(right)
    mean_b, std_b = _brightness_stats(im)
    cr = _color_ratios(im)

    layout = "unknown"
    if e_bottom > 0 and (e_top / max(1.0, e_bottom)) >= 1.25:
        layout = "top_band"
    elif e_right > 0 and (e_left / max(1.0, e_right)) >= 1.15:
        layout = "left_text"

    if layout == "top_band":
        composition = "上に文字帯、下に背景/被写体"
    elif layout == "left_text":
        composition = "左に大きな文字、右に被写体"
    else:
        composition = "文字と被写体を強調した高コントラスト構図"

    colors_bits: List[str] = []
    if cr.get("dark", 0.0) >= 0.25 or mean_b <= 70:
        colors_bits.append("黒基調")
    if cr.get("gold", 0.0) >= 0.01:
        colors_bits.append("金/黄アクセント")
    if cr.get("red", 0.0) >= 0.01:
        colors_bits.append("赤アクセント")
    if cr.get("white", 0.0) >= 0.02:
        colors_bits.append("白文字")
    colors = " + ".join(colors_bits) if colors_bits else "高コントラスト"

    design_elements: List[str] = []
    if layout == "top_band":
        design_elements.append("上帯テキスト")
    if layout == "left_text":
        design_elements.append("左大文字")
    if mean_b <= 70:
        design_elements.append("暗色背景")
    if std_b >= 55:
        design_elements.append("高コントラスト")
    if cr.get("gold", 0.0) >= 0.01:
        design_elements.append("金アクセント")
    if cr.get("red", 0.0) >= 0.01:
        design_elements.append("赤アクセント")
    if cr.get("white", 0.0) >= 0.02 and (cr.get("dark", 0.0) >= 0.15 or mean_b <= 90):
        design_elements.append("太字縁取り文字")

    hook_type = _infer_hook_type_from_title(title)

    title_clean = (title or "").strip()
    bracket = _extract_bracket_prefix(title_clean) or ""
    bracket_short = bracket.strip()
    if len(bracket_short) > 18:
        bracket_short = bracket_short[:18].rstrip()

    tags: List[str] = []
    # Always keep these for this genre.
    tags.extend(["仏教", "ブッダ"])
    if hook_type:
        tags.append(hook_type)
    if bracket_short:
        tags.append(bracket_short)

    # Keyword-based tags (very small dictionary; avoid overfitting).
    kw_tags = [
        ("人間関係", ["嫌い", "見下", "舐め", "悪口", "否定", "批判", "恩", "縁", "関わ", "無視"]),
        ("メンタル", ["心", "メンタル", "不安", "悩", "楽", "折れ", "心配", "クヨクヨ"]),
        ("幸せ", ["幸せ"]),
        ("運", ["運", "運気", "不運"]),
        ("会話術", ["会話", "話", "喋"]),
        ("習慣", ["習慣", "技術", "テクニック"]),
        ("自己防衛", ["守る", "受け流", "距離", "消す"]),
    ]
    for tag, needles in kw_tags:
        if any(n in title_clean for n in needles):
            tags.append(tag)

    # Fill to 8-16 tags with safe defaults if needed.
    defaults = ["人生", "考え方", "不安", "安心", "学び", "実践", "心を整える", "人間関係"]
    for d in defaults:
        if len(tags) >= 16:
            break
        if d not in tags:
            tags.append(d)
    tags = tags[:16]

    if any(k in title_clean for k in ["方法", "やり方", "技術", "テクニック"]):
        promise = "具体的な対処法がわかる"
    elif any(k in title_clean for k in ["理由", "なぜ"]):
        promise = "理由がわかる"
    elif "特徴" in title_clean:
        promise = "特徴がわかる"
    else:
        promise = "考え方がわかる"

    if "老後" in title_clean:
        target = "老後が不安な人"
    elif any(k in title_clean for k in ["嫌い", "見下", "舐め", "悪口", "否定", "批判", "人間関係"]):
        target = "人間関係で傷つきやすい人"
    elif "幸せ" in title_clean:
        target = "幸せを感じにくい人"
    elif "運" in title_clean:
        target = "運が悪いと感じる人"
    else:
        target = "心を整えたい人"

    if hook_type == "警告":
        emotion = "不安/警戒"
    elif hook_type == "暴露":
        emotion = "好奇心"
    elif hook_type == "質問":
        emotion = "好奇心"
    elif hook_type == "比較":
        emotion = "迷い"
    elif hook_type == "逆転":
        emotion = "驚き"
    elif hook_type == "共感":
        emotion = "共感/安心"
    elif hook_type == "断言":
        emotion = "安心"
    else:
        emotion = "内省"

    # Keep caption concrete but avoid inventing facts not visible.
    caption = f"{composition}。{hook_type}フックで「{promise}」を示し、{target}の{emotion}に刺さる設計。"
    if len(caption) > 220:
        caption = caption[:220].rstrip()

    payload = {
        "caption_ja": caption,
        "thumbnail_text": None,
        "hook_type": hook_type,
        "promise": promise,
        "target": target,
        "emotion": emotion,
        "composition": composition,
        "colors": colors,
        "design_elements": design_elements,
        "tags": tags,
        # Keep original meta for traceability (non-schema keys will be dropped by normalizer).
        "meta_title": title or None,
        "meta_view_count": view_count,
        "meta_duration_sec": duration_sec,
    }
    return _normalize_analysis_payload(payload)


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
        return int(value) if value.is_integer() else int(value)
    s = _safe_norm_str(value)
    if not s:
        return None
    try:
        return int(float(s))
    except Exception:
        return None


def _safe_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = _safe_norm_str(value)
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _as_text_content(content: Any) -> str:
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if isinstance(part, dict):
                t = part.get("text")
                if isinstance(t, str) and t.strip():
                    parts.append(t.strip())
        return " ".join(parts).strip()
    return str(content or "").strip()


def _collect_target_videos(report: Dict[str, Any], target: str, limit: int) -> List[Dict[str, Any]]:
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
        if not vid:
            continue
        if vid in seen:
            continue
        seen.add(vid)
        out.append(item)
        if limit and len(out) >= limit:
            break
    return out


def _summarize_insights(thumbnail_insights: Dict[str, Any]) -> Dict[str, Any]:
    tags_count: Dict[str, int] = {}
    hook_count: Dict[str, int] = {}

    for raw in (thumbnail_insights or {}).values():
        if not isinstance(raw, dict):
            continue
        analysis = raw.get("analysis")
        if not isinstance(analysis, dict):
            continue
        for tag in _normalize_short_list(analysis.get("tags"), max_items=80, max_len=18):
            tags_count[tag] = tags_count.get(tag, 0) + 1
        ht = _normalize_hook_type(analysis.get("hook_type"))
        if ht:
            hook_count[ht] = hook_count.get(ht, 0) + 1

    def _top_k(src: Dict[str, int], k: int) -> List[Dict[str, Any]]:
        return [{"value": key, "count": count} for key, count in sorted(src.items(), key=lambda kv: (-kv[1], kv[0]))[:k]]

    return {
        "schema": "ytm.yt_dlp.thumbnail_summary.v1",
        "generated_at": _utc_now_iso(),
        "insight_count": len([k for k in thumbnail_insights.keys()]) if isinstance(thumbnail_insights, dict) else 0,
        "top_tags": _top_k(tags_count, 30),
        "hook_types": _top_k(hook_count, 20),
    }


def _normalize_hook_type(value: Any) -> Optional[str]:
    raw = _safe_norm_str(value)
    if not raw:
        return None

    text = raw.replace("／", "/").replace("・", "/").replace("|", "/").strip()
    first = text.split("/", 1)[0].strip()
    if not first:
        return None

    allowed = ["警告", "暴露", "断言", "質問", "比較", "逆転", "共感", "その他"]
    if first in allowed:
        return first

    lower = first.lower()
    if "warn" in lower or "warning" in lower:
        return "警告"
    if "reveal" in lower or "expos" in lower:
        return "暴露"
    if "question" in lower or lower.endswith("?"):
        return "質問"
    if "compare" in lower or "vs" in lower:
        return "比較"

    hints = {
        "警告": ["警告", "注意", "危険", "ヤバ", "恐ろ", "怖"],
        "暴露": ["暴露", "裏", "真実", "闇", "実態", "本当"],
        "断言": ["断言", "結論", "必ず", "絶対", "確実"],
        "質問": ["質問", "なぜ", "どうして", "何", "？", "?"],
        "比較": ["比較", "VS", "vs", "違い", "どっち"],
        "逆転": ["逆転", "意外", "実は", "真逆", "誤解"],
        "共感": ["共感", "あるある", "悩み", "不安", "孤独", "つら"],
    }
    for canonical, needles in hints.items():
        if any(n in first for n in needles):
            return canonical

    return "その他"


def _normalize_short_list(value: Any, *, max_items: int, max_len: int) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    seen: set[str] = set()
    for raw in value:
        text = _safe_norm_str(raw)
        if not text:
            continue
        text = text.replace("\u3000", " ").strip()
        text = text.strip(" \t\r\n,，、。・")
        if not text:
            continue
        if len(text) > max_len:
            text = text[:max_len].rstrip()
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= max_items:
            break
    return out


def _normalize_analysis_payload(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}

    def norm_str(key: str) -> Optional[str]:
        text = _safe_norm_str(payload.get(key))
        if not text:
            return None
        text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        return text or None

    caption = norm_str("caption_ja")
    if caption and len(caption) > 220:
        caption = caption[:220].rstrip()

    thumb_text = norm_str("thumbnail_text")
    if thumb_text and len(thumb_text) > 1200:
        thumb_text = thumb_text[:1200].rstrip()

    return {
        "caption_ja": caption,
        "thumbnail_text": thumb_text,
        "hook_type": _normalize_hook_type(payload.get("hook_type")),
        "promise": norm_str("promise"),
        "target": norm_str("target"),
        "emotion": norm_str("emotion"),
        "composition": norm_str("composition"),
        "colors": norm_str("colors"),
        "design_elements": _normalize_short_list(payload.get("design_elements"), max_items=12, max_len=32),
        "tags": _normalize_short_list(payload.get("tags"), max_items=16, max_len=18),
    }


def _make_analysis_prompt(*, title: str, view_count: Optional[int], duration_sec: Optional[float]) -> str:
    meta = []
    if title:
        meta.append(f"- title: {title}")
    if view_count is not None:
        meta.append(f"- view_count: {view_count}")
    if duration_sec is not None:
        meta.append(f"- duration_sec: {duration_sec}")
    meta_block = "\n".join(meta) if meta else "（メタ情報なし）"

    return f"""次のYouTubeサムネイル画像を分析して、指定スキーマのJSONのみで返してください（文章は日本語）。

メタ情報（参考）:
{meta_block}

制約:
- 画像から読み取れない事実は断定しない（不明は null）。
- JSONのキーは固定（追加しない）。
- 抽象語だけで終わらせず、観察できる要素（人物/文字/配置/色/背景）を必ず入れる。

thumbnail_text:
- 画像内の文字をできるだけ「そのまま」抜き出す（改行は \\n で保持）。
- 判読不能なら null（推測しない）。

hook_type:
- 次のどれか1つだけを返す（必ずこの中から選ぶ）:
  警告 / 暴露 / 断言 / 質問 / 比較 / 逆転 / 共感 / その他

design_elements:
- 視覚/タイポ/レイアウトの短語を 3〜10 個（例: 太字縁取り文字 / 人物の顔アップ / 警告文 / 2分割 / 高コントラスト / 矢印）
- 1要素は 1〜6語程度に短くする（文章にしない）。

tags:
- 内容テーマ（悩み/学び/人間関係/老後など）の短語を 8〜16 個
- design_elements と重複しすぎない（役割を分ける）。

出力JSONスキーマ:
{{
  "caption_ja": "80〜140文字の具体説明",
  "thumbnail_text": "画像内の文字（改行は\\n、読めない場合はnull）",
  "hook_type": "警告/暴露/断言/質問/比較/逆転/共感/その他 のいずれか1つ",
  "promise": "サムネが約束していることを1文",
  "target": "想定視聴者（属性/悩み）を1文",
  "emotion": "喚起している感情（例: 不安/怒り/安心/好奇心）",
  "composition": "構図を1文（人物の有無/距離/背景/要素配置）",
  "colors": "色調の特徴を1文（例: 暖色/寒色/高コントラストなど）",
  "design_elements": ["要素（例: 太字縁取り文字）", "矢印", "顔のアップ"],
  "tags": ["検索/集計用の短いタグを8〜16個"]
}}
"""


def _analyze_thumbnail_with_llm(
    *,
    router: Any,
    thumbnail_url: str,
    title: str,
    view_count: Optional[int],
    duration_sec: Optional[float],
) -> Tuple[Dict[str, Any], Optional[str], str]:
    prompt = _make_analysis_prompt(title=title, view_count=view_count, duration_sec=duration_sec)
    messages: List[Dict[str, object]] = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": thumbnail_url}},
            ],
        }
    ]

    result = router.call_with_raw(
        task="visual_thumbnail_caption",
        messages=messages,
        system_prompt_override="あなたはYouTubeサムネイルのアナリストです。観察→抽出→要約を行い、必ずJSONのみで返します。",
        response_format="json_object",
        max_tokens=900,
        temperature=0.2,
    )

    provider = str(result.get("provider") or "").strip().lower()
    content_text = _as_text_content(result.get("content"))
    if provider == "agent" and not content_text:
        raise SystemExit("THINK MODE の結果がまだありません。agent_runner で完了してください。")
    if not content_text:
        raise RuntimeError("empty_content")

    try:
        payload = json.loads(content_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid_json: {exc}") from exc

    model = str(result.get("model") or "").strip() or None
    if provider == "agent":
        source = "think_mode"
    else:
        source = "openai" if provider == "azure" else "openrouter"
    if not isinstance(payload, dict):
        raise RuntimeError("json_is_not_object")
    payload = _normalize_analysis_payload(payload)
    return payload, model, source


@dataclass(frozen=True)
class TargetReport:
    channel_id: str
    path: Path


def _find_report_paths(*, channel_ids: List[str], all_reports: bool) -> List[TargetReport]:
    root = research_root() / YT_DLP_GENRE_DIR
    targets: List[TargetReport] = []

    if all_reports:
        for path in sorted(root.glob("*/report.json"), key=lambda p: p.as_posix()):
            channel_id = path.parent.name
            targets.append(TargetReport(channel_id=channel_id, path=path))
        return targets

    for cid in channel_ids:
        cleaned = cid.strip()
        if not cleaned:
            continue
        path = root / cleaned / "report.json"
        targets.append(TargetReport(channel_id=cleaned, path=path))
    return targets


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel-id", action="append", default=[], help="playlist_channel_id（report.jsonの親フォルダ名）")
    parser.add_argument("--all", action="store_true", help="全 report.json を対象にする")
    parser.add_argument("--target", choices=["top", "recent", "both", "all"], default="both", help="対象動画の取り方")
    parser.add_argument("--limit", type=int, default=20, help="1チャンネルあたり最大何本分析するか（default: 20）")
    parser.add_argument("--force", action="store_true", help="既存の分析を上書きする")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="ローカルの簡易解析（外部LLM/APIを呼ばない）を強制する（デフォルト挙動の明示用）",
    )
    parser.add_argument(
        "--use-llm",
        action="store_true",
        help="LLM(Vision)で解析する（外部LLM/APIを呼ぶ。デフォルトはオフライン簡易解析）",
    )
    parser.add_argument(
        "--fallback-offline",
        action="store_true",
        help="LLM解析が失敗した場合に、ローカルの簡易解析へフォールバックする",
    )
    parser.add_argument(
        "--continue-on-failover",
        action="store_true",
        help="LLM API が失敗して THINK MODE にフォールバックした場合でも、他の動画の処理を続ける（pendingを複数作り得る）",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="LLMを呼ばず、対象動画リストと既存分析の有無だけ表示して終了する（事故防止）",
    )
    parser.add_argument("--apply", action="store_true", help="report.json に書き込む（dry-run では書き込まない）")
    args = parser.parse_args()

    targets = _find_report_paths(channel_ids=args.channel_id, all_reports=bool(args.all))
    if not targets:
        raise SystemExit("no targets (use --channel-id or --all)")

    if args.list:
        for target in targets:
            if not target.path.exists():
                print(f"[skip] report.json not found: {target.path}")
                continue
            try:
                report = _read_json(target.path)
            except Exception as exc:
                print(f"[skip] failed to load json: {target.path} ({exc})")
                continue
            if not isinstance(report, dict):
                print(f"[skip] invalid report payload (not object): {target.path}")
                continue
            insights = report.get("thumbnail_insights")
            if not isinstance(insights, dict):
                insights = {}
            to_analyze = _collect_target_videos(report, target=args.target, limit=int(args.limit))
            print(f"\n# {target.channel_id} ({len(to_analyze)} candidates)")
            for item in to_analyze:
                vid = _safe_norm_str(item.get("id")) or "—"
                title = (_safe_norm_str(item.get("title")) or "").strip()
                status = "done" if vid in insights else "todo"
            print(f"- [{status}] {vid} {title}")
        return 0

    offline = bool(args.offline) or (not bool(args.use_llm))
    router = None
    if not offline:
        try:
            from factory_common.llm_router import get_router
        except Exception as exc:
            if args.fallback_offline:
                offline = True
            else:
                raise SystemExit(f"LLMRouter is not available: {exc}") from exc
        else:
            router = get_router()

    updated: List[Path] = []
    for target in targets:
        if not target.path.exists():
            print(f"[skip] report.json not found: {target.path}")
            continue

        try:
            report = _read_json(target.path)
        except Exception as exc:
            print(f"[skip] failed to load json: {target.path} ({exc})")
            continue
        if not isinstance(report, dict):
            print(f"[skip] invalid report payload (not object): {target.path}")
            continue

        insights = report.get("thumbnail_insights")
        if not isinstance(insights, dict):
            insights = {}

        to_analyze = _collect_target_videos(report, target=args.target, limit=int(args.limit))
        if not to_analyze:
            print(f"[skip] no candidate videos: {target.path}")
            continue

        wrote_any = False
        for item in to_analyze:
            vid = _safe_norm_str(item.get("id"))
            if not vid:
                continue
            if not args.force and vid in insights:
                continue

            thumb_url_raw = _safe_norm_str(item.get("thumbnail_url")) or _safe_norm_str(item.get("thumbnail"))
            if not thumb_url_raw:
                continue
            thumb_url = _ytimg_hqdefault_url(vid)

            title = _safe_norm_str(item.get("title")) or ""
            view_count = _safe_int(item.get("view_count"))
            duration_sec = _safe_float(item.get("duration_sec"))

            try:
                if offline:
                    analysis = _analyze_thumbnail_offline(
                        thumbnail_url=thumb_url,
                        title=title,
                        view_count=view_count,
                        duration_sec=duration_sec,
                    )
                    model = None
                    source = "offline_heuristic"
                else:
                    assert router is not None
                    analysis, model, source = _analyze_thumbnail_with_llm(
                        router=router,
                        thumbnail_url=thumb_url,
                        title=title,
                        view_count=view_count,
                        duration_sec=duration_sec,
                    )
            except SystemExit as exc:
                if not offline and args.fallback_offline:
                    try:
                        analysis = _analyze_thumbnail_offline(
                            thumbnail_url=thumb_url,
                            title=title,
                            view_count=view_count,
                            duration_sec=duration_sec,
                        )
                        model = None
                        source = "offline_heuristic"
                    except Exception as exc2:
                        print(f"[warn] offline analysis failed: {target.channel_id}/{vid} ({exc2})")
                        continue
                elif offline:
                    print(f"[warn] offline analysis aborted: {target.channel_id}/{vid} ({exc})")
                    continue
                if args.continue_on_failover:
                    first_line = str(exc).splitlines()[0] if str(exc) else "THINK MODE (queued)"
                    print(f"[queue] {target.channel_id}/{vid} {first_line}")
                    continue
                raise
            except Exception as exc:
                if not offline and args.fallback_offline:
                    try:
                        analysis = _analyze_thumbnail_offline(
                            thumbnail_url=thumb_url,
                            title=title,
                            view_count=view_count,
                            duration_sec=duration_sec,
                        )
                        model = None
                        source = "offline_heuristic"
                    except Exception as exc2:
                        print(f"[warn] analysis failed: {target.channel_id}/{vid} ({exc}); offline also failed: ({exc2})")
                        continue
                else:
                    print(f"[warn] analysis failed: {target.channel_id}/{vid} ({exc})")
                    continue

            insights[vid] = {
                "schema": "ytm.yt_dlp.thumbnail_insight.v1",
                "generated_at": _utc_now_iso(),
                "source": source,
                "model": model,
                "video": {
                    "id": vid,
                    "title": title or None,
                    "url": _safe_norm_str(item.get("url")),
                    "view_count": view_count,
                    "duration_sec": duration_sec,
                    "thumbnail_url": thumb_url_raw,
                },
                "analysis": analysis,
            }
            wrote_any = True
            print(f"[ok] analyzed: {target.channel_id}/{vid}")

        next_summary = _summarize_insights(insights)
        prev_summary = report.get("thumbnail_summary")

        def _strip_generated_at(value: Any) -> Any:
            if not isinstance(value, dict):
                return None
            stripped = dict(value)
            stripped.pop("generated_at", None)
            return stripped

        summary_changed = _strip_generated_at(prev_summary) != _strip_generated_at(next_summary)
        if summary_changed:
            report["thumbnail_summary"] = next_summary

        if not wrote_any and not summary_changed:
            print(f"[ok] up-to-date: {target.path}")
            continue

        report["thumbnail_insights"] = insights
        if not summary_changed:
            report["thumbnail_summary"] = next_summary

        if args.apply:
            _write_json(target.path, report)
            updated.append(target.path)
            print(f"[write] {target.path}")
        else:
            print(f"[dry-run] would write: {target.path}")

    if updated and args.apply:
        print("")
        print("updated:")
        for path in updated:
            rel = path.relative_to(research_root())
            print(f"- workspaces/research/{rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

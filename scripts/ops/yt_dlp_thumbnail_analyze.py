#!/usr/bin/env python3
"""
yt_dlp_thumbnail_analyze.py — yt-dlpベンチマーク（research）に「サムネ言語化/分析」を付与する。

前提:
  - `scripts/ops/yt_dlp_benchmark_analyze.py` が生成した
    `workspaces/research/YouTubeベンチマーク（yt-dlp）/*/report.json` を入力（SoT）として扱う。
  - 本スクリプトは LLM(Vision) を使って、サムネの内容を「言語化」して JSON に保存する。

設計方針（事故防止）:
  - 既存 report.json の構造は壊さず、追加キーとして `thumbnail_insights` / `thumbnail_summary` を付与する。
  - 1チャンネルにつき対象動画数は絞る（デフォルト: top_by_views + recent のユニオン最大 20件）。
  - 既に分析済みの動画は再実行しない（--force で上書き）。

Usage:
  # 1チャンネルだけ
  python3 scripts/ops/yt_dlp_thumbnail_analyze.py --channel-id UCOmPg-Ncs7XA5Jt_JBUJDmg --apply

  # 全チャンネル
  python3 scripts/ops/yt_dlp_thumbnail_analyze.py --all --apply
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

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

    try:
        from factory_common.llm_router import get_router
    except Exception as exc:
        raise SystemExit(f"LLMRouter is not available: {exc}") from exc

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
                analysis, model, source = _analyze_thumbnail_with_llm(
                    router=router,
                    thumbnail_url=thumb_url,
                    title=title,
                    view_count=view_count,
                    duration_sec=duration_sec,
                )
            except SystemExit as exc:
                if args.continue_on_failover:
                    first_line = str(exc).splitlines()[0] if str(exc) else "THINK MODE (queued)"
                    print(f"[queue] {target.channel_id}/{vid} {first_line}")
                    continue
                raise
            except Exception as exc:
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

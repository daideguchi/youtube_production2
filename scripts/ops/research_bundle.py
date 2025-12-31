from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from factory_common.paths import video_root


RESEARCH_BUNDLE_SCHEMA = "ytm.research_bundle.v1"
WEB_SEARCH_SCHEMA = "ytm.web_search_results.v1"
WIKIPEDIA_SUMMARY_SCHEMA = "ytm.wikipedia_summary.v1"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _norm_channel(channel: str) -> str:
    return str(channel or "").strip().upper()


def _norm_video(video: str) -> str:
    return str(video or "").strip().zfill(3)


def _is_http_url(value: str) -> bool:
    v = str(value or "").strip().lower()
    return v.startswith("http://") or v.startswith("https://")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, payload: Any) -> None:
    _write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _normalize_search_results(obj: Any, *, query_default: str) -> Tuple[Dict[str, Any], List[str]]:
    errors: List[str] = []
    if obj is None:
        obj = {}
    if isinstance(obj, list):
        obj = {"hits": obj}
    if not isinstance(obj, dict):
        return (
            {
                "schema": WEB_SEARCH_SCHEMA,
                "provider": "disabled",
                "query": str(query_default or "").strip(),
                "retrieved_at": _utc_now_iso(),
                "hits": [],
            },
            ["search_results must be an object"],
        )

    provider = str(obj.get("provider") or "manual").strip() or "manual"
    query = str(obj.get("query") or query_default or "").strip()
    retrieved_at = str(obj.get("retrieved_at") or _utc_now_iso()).strip()
    hits_in = obj.get("hits") or []
    hits_out: List[Dict[str, Any]] = []
    if not isinstance(hits_in, list):
        errors.append("search_results.hits must be a list")
        hits_in = []
    for idx, hit in enumerate(hits_in):
        if isinstance(hit, str):
            hit = {"title": hit, "url": hit}
        if not isinstance(hit, dict):
            errors.append(f"search_results.hits[{idx}] must be an object")
            continue
        url = str(hit.get("url") or "").strip()
        title = str(hit.get("title") or url).strip() or url
        if not _is_http_url(url):
            errors.append(f"search_results.hits[{idx}].url must be http/https")
            continue
        hits_out.append(
            {
                "title": title,
                "url": url,
                "snippet": (str(hit.get("snippet")).strip() if hit.get("snippet") is not None else None),
                "source": (str(hit.get("source")).strip() if hit.get("source") is not None else None),
                "age": (str(hit.get("age")).strip() if hit.get("age") is not None else None),
            }
        )

    out = {
        "schema": WEB_SEARCH_SCHEMA,
        "provider": provider,
        "query": query,
        "retrieved_at": retrieved_at,
        "hits": hits_out,
    }
    return out, errors


def _normalize_wikipedia_summary(obj: Any, *, query_default: str, lang_default: str = "ja") -> Tuple[Dict[str, Any], List[str]]:
    errors: List[str] = []
    if obj is None:
        obj = {}
    if not isinstance(obj, dict):
        obj = {}
        errors.append("wikipedia_summary must be an object")

    provider = str(obj.get("provider") or "manual").strip() or "manual"
    query = str(obj.get("query") or query_default or "").strip()
    lang = str(obj.get("lang") or lang_default or "ja").strip().lower() or "ja"
    retrieved_at = str(obj.get("retrieved_at") or _utc_now_iso()).strip()

    page_url = obj.get("page_url")
    if page_url is not None and page_url != "" and not _is_http_url(str(page_url)):
        errors.append("wikipedia_summary.page_url must be http/https or null")
        page_url = None

    page_id = obj.get("page_id")
    try:
        page_id = int(page_id) if page_id is not None else None
    except Exception:
        page_id = None

    out = {
        "schema": WIKIPEDIA_SUMMARY_SCHEMA,
        "provider": provider,
        "query": query,
        "lang": lang,
        "retrieved_at": retrieved_at,
        "page_title": (str(obj.get("page_title")).strip() if obj.get("page_title") is not None else None),
        "page_id": page_id,
        "page_url": (str(page_url).strip() if page_url is not None else None),
        "extract": (str(obj.get("extract")).strip() if obj.get("extract") is not None else None),
    }
    return out, errors


def _normalize_references(obj: Any, *, search_results: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    errors: List[str] = []
    entries: List[Dict[str, Any]] = []

    if obj is None:
        obj = []
    if not isinstance(obj, list):
        errors.append("references must be a list")
        obj = []

    for idx, it in enumerate(obj):
        if isinstance(it, str):
            it = {"title": it, "url": it}
        if not isinstance(it, dict):
            errors.append(f"references[{idx}] must be an object")
            continue
        url = str(it.get("url") or "").strip()
        if not _is_http_url(url):
            errors.append(f"references[{idx}].url must be http/https")
            continue
        title = str(it.get("title") or url).strip() or url
        entry: Dict[str, Any] = {
            "title": title,
            "url": url,
            "type": str(it.get("type") or "web").strip() or "web",
            "source": (str(it.get("source")).strip() if it.get("source") is not None else ""),
            "year": None,
            "note": (str(it.get("note")).strip() if it.get("note") is not None else ""),
            "confidence": it.get("confidence"),
        }
        year = it.get("year")
        try:
            entry["year"] = int(year) if year is not None else None
        except Exception:
            entry["year"] = None
        try:
            entry["confidence"] = float(entry["confidence"]) if entry.get("confidence") is not None else None
        except Exception:
            entry["confidence"] = None
        entries.append(entry)

    # If references are empty, auto-seed from search hits (best-effort).
    if not entries:
        hits = search_results.get("hits") if isinstance(search_results, dict) else None
        if isinstance(hits, list):
            seen: set[str] = set()
            for h in hits:
                if not isinstance(h, dict):
                    continue
                url = str(h.get("url") or "").strip()
                if not _is_http_url(url) or url in seen:
                    continue
                seen.add(url)
                entries.append(
                    {
                        "title": str(h.get("title") or url).strip() or url,
                        "url": url,
                        "type": "web",
                        "source": (str(h.get("source")).strip() if h.get("source") is not None else ""),
                        "year": None,
                        "note": "search_results.hits から自動抽出",
                        "confidence": 0.35,
                    }
                )
                if len(entries) >= 10:
                    break

    return entries, errors


def _template_bundle(channel: str, video: str, *, topic: str | None) -> Dict[str, Any]:
    ch = _norm_channel(channel)
    no = _norm_video(video)
    topic_norm = str(topic or "").strip()
    now = _utc_now_iso()
    return {
        "schema": RESEARCH_BUNDLE_SCHEMA,
        "generated_at": now,
        "channel": ch,
        "video": no,
        "topic": topic_norm,
        "search_results": {
            "schema": WEB_SEARCH_SCHEMA,
            "provider": "manual",
            "query": topic_norm,
            "retrieved_at": now,
            "hits": [],
        },
        "wikipedia_summary": {
            "schema": WIKIPEDIA_SUMMARY_SCHEMA,
            "provider": "manual",
            "query": topic_norm,
            "lang": "ja",
            "retrieved_at": now,
            "page_title": None,
            "page_id": None,
            "page_url": None,
            "extract": None,
        },
        "references": [],
        "research_brief_md": "# Research Brief\n\n- 主要な論点:\n- 反証/注意点:\n- 使える具体例:\n",
    }


def cmd_template(args: argparse.Namespace) -> int:
    payload = _template_bundle(args.channel, args.video, topic=args.topic)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    bundle_path = Path(args.bundle).expanduser().resolve()
    obj = _read_json(bundle_path)
    if not isinstance(obj, dict):
        raise SystemExit("bundle must be a JSON object")
    if str(obj.get("schema") or "").strip() != RESEARCH_BUNDLE_SCHEMA:
        raise SystemExit(f"bundle.schema must be {RESEARCH_BUNDLE_SCHEMA}")

    ch = _norm_channel(args.channel or obj.get("channel") or "")
    no = _norm_video(args.video or obj.get("video") or "")
    if not ch or not no:
        raise SystemExit("channel/video is required (in bundle or via --channel/--video)")

    topic = str(obj.get("topic") or "").strip()
    search_results, e1 = _normalize_search_results(obj.get("search_results"), query_default=topic)
    wikipedia_summary, e2 = _normalize_wikipedia_summary(obj.get("wikipedia_summary"), query_default=topic, lang_default="ja")
    references, e3 = _normalize_references(obj.get("references"), search_results=search_results)
    brief = obj.get("research_brief_md")
    if brief is None:
        brief = ""
    if not isinstance(brief, str):
        e3.append("research_brief_md must be a string")
        brief = ""

    errors = [*e1, *e2, *e3]
    if errors:
        print(json.dumps({"ok": False, "errors": errors}, ensure_ascii=False, indent=2))
        return 2

    base = video_root(ch, no)
    research_dir = base / "content" / "analysis" / "research"
    out_search = research_dir / "search_results.json"
    out_wiki = research_dir / "wikipedia_summary.json"
    out_refs = research_dir / "references.json"
    out_brief = research_dir / "research_brief.md"

    if not args.dry_run:
        _write_json(out_search, search_results)
        _write_json(out_wiki, wikipedia_summary)
        _write_json(out_refs, references)
        _write_text(out_brief, brief.strip() + ("\n" if brief.strip() else ""))

    print(
        json.dumps(
            {
                "ok": True,
                "dry_run": bool(args.dry_run),
                "channel": ch,
                "video": no,
                "written": {
                    "search_results": str(out_search),
                    "wikipedia_summary": str(out_wiki),
                    "references": str(out_refs),
                    "research_brief": str(out_brief),
                },
                "counts": {
                    "search_hits": len(search_results.get("hits") or []),
                    "references": len(references),
                    "brief_chars": len(brief.strip()),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Create/apply research bundle (manual injection) for topic_research.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ap_t = sub.add_parser("template", help="Print a research bundle JSON template to stdout")
    ap_t.add_argument("--channel", required=True)
    ap_t.add_argument("--video", required=True)
    ap_t.add_argument("--topic", default=None)
    ap_t.set_defaults(func=cmd_template)

    ap_a = sub.add_parser("apply", help="Apply a research bundle JSON to workspaces/scripts/{CH}/{NNN}/...")
    ap_a.add_argument("--bundle", required=True)
    ap_a.add_argument("--channel", default=None, help="Override channel in bundle")
    ap_a.add_argument("--video", default=None, help="Override video in bundle")
    ap_a.add_argument("--dry-run", action="store_true")
    ap_a.set_defaults(func=cmd_apply)

    args = ap.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())


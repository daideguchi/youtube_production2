from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests


class WikipediaError(RuntimeError):
    pass


class WikipediaHttpError(WikipediaError):
    def __init__(self, *, status_code: int, body_snippet: str) -> None:
        super().__init__(f"wikipedia request failed: status={status_code} body={body_snippet}")
        self.status_code = status_code
        self.body_snippet = body_snippet


WIKIPEDIA_SUMMARY_SCHEMA = "ytm.wikipedia_summary.v1"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _wikipedia_api(lang: str) -> str:
    return f"https://{lang}.wikipedia.org/w/api.php"


def _user_agent() -> str:
    # MediaWiki API recommends setting a descriptive UA.
    return os.getenv("YTM_HTTP_USER_AGENT") or "factory_commentary/1.0 (+https://github.com/daideguchi/youtube_production2)"


@dataclass(frozen=True)
class WikipediaSummary:
    provider: str
    query: str
    lang: str
    retrieved_at: str
    page_title: str | None = None
    page_id: int | None = None
    page_url: str | None = None
    extract: str | None = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema": WIKIPEDIA_SUMMARY_SCHEMA,
            "provider": self.provider,
            "query": self.query,
            "lang": self.lang,
            "retrieved_at": self.retrieved_at,
            "page_title": self.page_title,
            "page_id": self.page_id,
            "page_url": self.page_url,
            "extract": self.extract,
        }


def _http_get_json(url: str, *, params: Dict[str, Any], timeout_s: int) -> Dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "User-Agent": _user_agent(),
    }
    resp = requests.get(url, params=params, headers=headers, timeout=timeout_s)
    if resp.status_code != 200:
        body = (resp.text or "").strip().replace("\n", " ")
        raise WikipediaHttpError(status_code=int(resp.status_code), body_snippet=body[:200])
    try:
        data = resp.json() or {}
    except Exception as exc:
        raise WikipediaError(f"wikipedia response is not json: {url}") from exc
    return data if isinstance(data, dict) else {}


def _search_best_title(*, query: str, lang: str, timeout_s: int) -> str | None:
    data = _http_get_json(
        _wikipedia_api(lang),
        params={
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": 1,
            "format": "json",
            "utf8": 1,
        },
        timeout_s=timeout_s,
    )
    items = (((data.get("query") or {}).get("search")) or [])
    if not isinstance(items, list) or not items:
        return None
    first = items[0] if isinstance(items[0], dict) else {}
    title = str(first.get("title") or "").strip()
    return title or None


def _fetch_intro_page(*, title: str, lang: str, timeout_s: int) -> WikipediaSummary:
    data = _http_get_json(
        _wikipedia_api(lang),
        params={
            "action": "query",
            "prop": "extracts|info",
            "explaintext": 1,
            "exintro": 1,
            "redirects": 1,
            "inprop": "url",
            "titles": title,
            "format": "json",
            "utf8": 1,
        },
        timeout_s=timeout_s,
    )
    pages = (((data.get("query") or {}).get("pages")) or {})
    if not isinstance(pages, dict) or not pages:
        return WikipediaSummary(provider="wikipedia", query=title, lang=lang, retrieved_at=_utc_now_iso())

    page: Optional[Dict[str, Any]] = None
    for v in pages.values():
        if isinstance(v, dict):
            page = v
            break
    if not isinstance(page, dict) or page.get("missing") is not None:
        return WikipediaSummary(provider="wikipedia", query=title, lang=lang, retrieved_at=_utc_now_iso())

    page_title = str(page.get("title") or "").strip() or None
    page_url = str(page.get("fullurl") or "").strip() or None
    extract = str(page.get("extract") or "").strip() or None
    try:
        page_id = int(page.get("pageid")) if page.get("pageid") is not None else None
    except Exception:
        page_id = None

    return WikipediaSummary(
        provider="wikipedia",
        query=title,
        lang=lang,
        retrieved_at=_utc_now_iso(),
        page_title=page_title,
        page_id=page_id,
        page_url=page_url,
        extract=extract,
    )


def fetch_wikipedia_intro(
    query: str,
    *,
    lang: str = "ja",
    fallback_lang: str | None = "en",
    timeout_s: int = 20,
) -> WikipediaSummary:
    """
    Resolve query to a Wikipedia page (via search) and return intro extract + URL.

    Best-effort:
    - Network/parse failures are surfaced as exceptions (caller can catch and downgrade to disabled).
    - "Not found" returns a summary with empty fields (no exception).
    """
    q = str(query or "").strip()
    if not q:
        return WikipediaSummary(provider="wikipedia", query="", lang=str(lang or "ja"), retrieved_at=_utc_now_iso())

    lang_norm = str(lang or "ja").strip().lower() or "ja"
    resolved = _search_best_title(query=q, lang=lang_norm, timeout_s=int(timeout_s))
    if resolved:
        return _fetch_intro_page(title=resolved, lang=lang_norm, timeout_s=int(timeout_s))

    fb = str(fallback_lang or "").strip().lower()
    if fb and fb != lang_norm:
        resolved_fb = _search_best_title(query=q, lang=fb, timeout_s=int(timeout_s))
        if resolved_fb:
            return _fetch_intro_page(title=resolved_fb, lang=fb, timeout_s=int(timeout_s))

    return WikipediaSummary(provider="wikipedia", query=q, lang=lang_norm, retrieved_at=_utc_now_iso())

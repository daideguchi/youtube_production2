from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests


class WebSearchError(RuntimeError):
    pass


class MissingWebSearchApiKey(WebSearchError):
    pass


class WebSearchHttpError(WebSearchError):
    def __init__(self, *, provider: str, status_code: int, body_snippet: str) -> None:
        super().__init__(f"{provider} search failed: status={status_code} body={body_snippet}")
        self.provider = provider
        self.status_code = status_code
        self.body_snippet = body_snippet


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


WEB_SEARCH_SCHEMA = "ytm.web_search_results.v1"


@dataclass(frozen=True)
class WebSearchHit:
    title: str
    url: str
    snippet: str | None = None
    source: str | None = None
    age: str | None = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source": self.source,
            "age": self.age,
        }


@dataclass(frozen=True)
class WebSearchResult:
    provider: str
    query: str
    retrieved_at: str
    hits: List[WebSearchHit]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema": WEB_SEARCH_SCHEMA,
            "provider": self.provider,
            "query": self.query,
            "retrieved_at": self.retrieved_at,
            "hits": [h.as_dict() for h in self.hits],
        }


BRAVE_WEB_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


def _normalize_provider(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    return raw.replace("-", "_")


def _is_disabled_provider(value: str | None) -> bool:
    return _normalize_provider(value) in {"", "0", "off", "false", "disabled", "none", "no"}


def _strip_code_fences(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9_-]*\\n?", "", s).strip()
        if s.endswith("```"):
            s = s[: -3].rstrip()
    return s


def _extract_json_object(text: str) -> Dict[str, Any] | None:
    s = _strip_code_fences(text)
    if not s:
        return None
    if s.startswith("{") and s.endswith("}"):
        try:
            obj = json.loads(s)
            return obj if isinstance(obj, dict) else None
        except Exception:
            pass
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end < 0 or end <= start:
        return None
    chunk = s[start : end + 1]
    try:
        obj = json.loads(chunk)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def brave_web_search(
    query: str,
    *,
    count: int = 8,
    country: str = "JP",
    search_lang: str = "ja",
    safesearch: str = "moderate",
    freshness: str | None = None,
    timeout_s: int = 20,
) -> WebSearchResult:
    api_key = os.getenv("BRAVE_SEARCH_API_KEY") or os.getenv("BRAVE_API_KEY")
    if not api_key:
        raise MissingWebSearchApiKey("BRAVE_SEARCH_API_KEY is not set")

    q = (query or "").strip()
    if not q:
        raise ValueError("query is empty")

    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": api_key,
        "User-Agent": "factory_commentary/1.0 (+https://github.com/)",
    }
    params: Dict[str, Any] = {
        "q": q,
        "count": int(count),
        "country": country,
        "search_lang": search_lang,
        "safesearch": safesearch,
    }
    if freshness:
        params["freshness"] = str(freshness)

    resp = requests.get(BRAVE_WEB_ENDPOINT, headers=headers, params=params, timeout=timeout_s)
    if resp.status_code != 200:
        body = (resp.text or "").strip().replace("\n", " ")
        raise WebSearchHttpError(provider="brave", status_code=int(resp.status_code), body_snippet=body[:200])

    payload = resp.json() or {}
    web = payload.get("web") if isinstance(payload, dict) else {}
    results = (web or {}).get("results") if isinstance(web, dict) else []
    hits: List[WebSearchHit] = []
    if isinstance(results, list):
        for item in results:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            title = str(item.get("title") or "").strip()
            if not url:
                continue
            snippet = str(item.get("description") or "").strip() or None
            age = str(item.get("age") or "").strip() or None
            source: Optional[str] = None
            profile = item.get("profile")
            if isinstance(profile, dict):
                source = str(profile.get("name") or "").strip() or None
            hits.append(WebSearchHit(title=title or url, url=url, snippet=snippet, source=source, age=age))

    return WebSearchResult(provider="brave", query=q, retrieved_at=_utc_now_iso(), hits=hits)


def openrouter_web_search(
    query: str,
    *,
    count: int = 8,
    model: str | None = None,
    timeout_s: int = 30,
) -> WebSearchResult:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise MissingWebSearchApiKey("OPENROUTER_API_KEY is not set")

    q = (query or "").strip()
    if not q:
        raise ValueError("query is empty")

    model_override = (model or os.getenv("YTM_WEB_SEARCH_OPENROUTER_MODEL") or "").strip() or None
    task = (os.getenv("YTM_WEB_SEARCH_OPENROUTER_TASK") or "web_search_openrouter").strip() or "web_search_openrouter"

    try:
        from factory_common.llm_router import get_router
    except Exception as exc:  # pragma: no cover - optional dependency mismatch
        raise WebSearchError(f"LLMRouter is not available: {exc}") from exc

    router = get_router()
    prompt = (
        "以下のクエリでWeb検索した結果を、厳密なJSONのみで返してください。\n"
        f"- 最大{int(count)}件\n"
        "- URLは検索で実際に見つかったものだけ（捏造禁止）。不確かな場合は省略。\n"
        "- 形式: {\"hits\": [{\"title\": str, \"url\": str, \"snippet\": str|null, \"source\": str|null, \"age\": str|null}]}\n"
        f"\nquery: {q}\n"
    )
    result = router.call_with_raw(
        task=task,
        messages=[
            {"role": "system", "content": "You are a web search assistant. Output JSON only."},
            {"role": "user", "content": prompt},
        ],
        model_keys=[model_override] if model_override else None,
        timeout=int(timeout_s),
    )
    content = result.get("content")
    if isinstance(content, list):
        text = " ".join(str(part.get("text", "")).strip() for part in content if isinstance(part, dict)).strip()
    else:
        text = str(content or "").strip()

    obj = _extract_json_object(text) or {}
    raw_hits = obj.get("hits") or obj.get("results") or []
    hits: List[WebSearchHit] = []
    seen: set[str] = set()
    if isinstance(raw_hits, list):
        for item in raw_hits:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or item.get("link") or "").strip()
            if not url or not (url.startswith("http://") or url.startswith("https://")):
                continue
            if url in seen:
                continue
            seen.add(url)
            title = str(item.get("title") or item.get("name") or url).strip()
            snippet = str(item.get("snippet") or item.get("description") or item.get("summary") or "").strip() or None
            source = str(item.get("source") or item.get("site") or item.get("publisher") or "").strip() or None
            age = str(item.get("age") or item.get("date") or item.get("published") or "").strip() or None
            hits.append(WebSearchHit(title=title or url, url=url, snippet=snippet, source=source, age=age))
            if len(hits) >= int(count):
                break

    provider = f"llm_router:{result.get('provider') or 'openrouter'}:{result.get('model') or (model_override or 'default')}"
    return WebSearchResult(provider=provider, query=q, retrieved_at=_utc_now_iso(), hits=hits)


def web_search(
    query: str,
    *,
    provider: str | None = None,
    count: int = 8,
    timeout_s: int = 20,
) -> WebSearchResult:
    """
    Unified web search helper.

    provider:
      - auto (default): brave if BRAVE_SEARCH_API_KEY else openrouter if OPENROUTER_API_KEY else disabled
      - brave
      - openrouter
      - disabled
    """
    prov = _normalize_provider(provider or os.getenv("YTM_WEB_SEARCH_PROVIDER") or "auto")
    if _is_disabled_provider(prov):
        q = (query or "").strip()
        return WebSearchResult(provider="disabled", query=q, retrieved_at=_utc_now_iso(), hits=[])

    if prov == "auto":
        if os.getenv("BRAVE_SEARCH_API_KEY") or os.getenv("BRAVE_API_KEY"):
            return brave_web_search(query, count=count, timeout_s=timeout_s)
        if os.getenv("OPENROUTER_API_KEY"):
            return openrouter_web_search(query, count=count, timeout_s=max(int(timeout_s), 20))
        q = (query or "").strip()
        return WebSearchResult(provider="disabled", query=q, retrieved_at=_utc_now_iso(), hits=[])

    if prov == "brave":
        return brave_web_search(query, count=count, timeout_s=timeout_s)

    if prov in {"openrouter", "openrouter_sonar"}:
        return openrouter_web_search(query, count=count, timeout_s=max(int(timeout_s), 20))

    raise ValueError(f"Unknown web search provider: {provider}")

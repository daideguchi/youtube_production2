from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional

import requests

from factory_common.routing_lockdown import lockdown_active


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


def _web_search_openrouter_task() -> str:
    return (os.getenv("YTM_WEB_SEARCH_OPENROUTER_TASK") or "web_search_openrouter").strip() or "web_search_openrouter"


def _openrouter_search_messages(query: str, *, count: int) -> List[Dict[str, str]]:
    q = (query or "").strip()
    prompt = (
        "以下のクエリでWeb検索した結果を、厳密なJSONのみで返してください。\n"
        f"- 最大{int(count)}件\n"
        "- URLは検索で実際に見つかったものだけ（捏造禁止）。不確かな場合は省略。\n"
        "- 形式: {\"hits\": [{\"title\": str, \"url\": str, \"snippet\": str|null, \"source\": str|null, \"age\": str|null}]}\n"
        f"\nquery: {q}\n"
    )
    return [
        {"role": "system", "content": "You are a web search assistant. Output JSON only."},
        {"role": "user", "content": prompt},
    ]


@lru_cache(maxsize=1)
def _load_llm_router_config() -> Dict[str, Any]:
    """
    Best-effort loader for configs/llm_router.yaml (no secrets).
    Used to keep agent-queued tasks consistent with router defaults (task_id stability).
    """
    try:
        import yaml

        from factory_common import paths as repo_paths

        path = repo_paths.repo_root() / "configs" / "llm_router.yaml"
        if not path.exists():
            return {}
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _router_task_plan(task: str) -> tuple[List[str], Dict[str, Any], Optional[str]]:
    """
    Returns: (model_chain, options_defaults, response_format)
    """
    cfg = _load_llm_router_config()
    tasks = cfg.get("tasks") if isinstance(cfg, dict) else None
    ent = (tasks or {}).get(task) if isinstance(tasks, dict) else None
    ent = ent if isinstance(ent, dict) else {}
    tier = str(ent.get("tier") or "").strip()
    defaults = ent.get("defaults") if isinstance(ent.get("defaults"), dict) else {}
    response_format = defaults.get("response_format") if isinstance(defaults, dict) else None

    tiers = cfg.get("tiers") if isinstance(cfg, dict) else None
    chain_raw = (tiers or {}).get(tier) if isinstance(tiers, dict) else None
    chain: List[str] = []
    if isinstance(chain_raw, list):
        for x in chain_raw:
            s = str(x or "").strip()
            if s:
                chain.append(s)

    opts: Dict[str, Any] = {}
    if isinstance(defaults, dict):
        opts.update(defaults)
    # Keep task_id stable with router/failover artifacts.
    if chain:
        opts["_model_chain"] = chain
    return chain, opts, str(response_format).strip() if response_format not in (None, "") else None


def agent_queue_web_search(
    query: str,
    *,
    count: int = 8,
) -> WebSearchResult:
    """
    Queue-only web search: do not call external APIs.

    - If cached agent results exist, reuse them.
    - Otherwise, create a pending agent task and stop (SystemExit).
    """
    q = (query or "").strip()
    if not q:
        raise ValueError("query is empty")

    task = _web_search_openrouter_task()
    messages = _openrouter_search_messages(q, count=int(count))
    _, options, response_format = _router_task_plan(task)

    try:
        from factory_common.agent_mode import (
            compute_task_id,
            ensure_pending_task,
            get_queue_dir,
            pending_path,
            read_result_content,
            results_path,
        )
    except Exception as exc:  # pragma: no cover - optional dependency mismatch
        raise WebSearchError(f"agent_mode is not available: {exc}") from exc

    queue_dir = get_queue_dir()
    task_id = compute_task_id(task, messages, options)
    r_path = results_path(task_id, queue_dir=queue_dir)
    if r_path.exists():
        content = read_result_content(task_id, queue_dir=queue_dir)
        obj = _extract_json_object(content) or {}
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
        return WebSearchResult(provider=f"agent_queue:{task}", query=q, retrieved_at=_utc_now_iso(), hits=hits)

    ensure_pending_task(
        task_id=task_id,
        task=task,
        messages=messages,
        options=options,
        response_format=response_format,
        queue_dir=queue_dir,
    )
    p_path = pending_path(task_id, queue_dir=queue_dir)
    msg_lines = [
        "[WEB_SEARCH_AGENT] Web search queued (no external API call).",
        f"- task_id: {task_id}",
        f"- task: {task}",
        f"- pending: {p_path}",
        f"- expected result: {r_path}",
        "- next:",
        "  - python scripts/agent_runner.py show " + task_id,
        "  - follow the runbook/messages, then:",
        "    python scripts/agent_runner.py complete " + task_id + " --content-file /path/to/content.txt",
        "  - rerun: the same pipeline command",
    ]
    raise SystemExit("\n".join(msg_lines))


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
    if model_override and lockdown_active():
        # Drift prevention: model selection must be controlled via numeric slots, not per-call overrides.
        model_override = None
    task = _web_search_openrouter_task()

    try:
        from factory_common.llm_router import get_router
    except Exception as exc:  # pragma: no cover - optional dependency mismatch
        raise WebSearchError(f"LLMRouter is not available: {exc}") from exc

    router = get_router()
    messages = _openrouter_search_messages(q, count=int(count))
    result = router.call_with_raw(
        task=task,
        messages=messages,
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
      - agent (queue-only; no external API call)
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

    if prov in {"agent", "queue"}:
        return agent_queue_web_search(query, count=count)

    if prov == "brave":
        return brave_web_search(query, count=count, timeout_s=timeout_s)

    if prov in {"openrouter", "openrouter_sonar"}:
        return openrouter_web_search(query, count=count, timeout_s=max(int(timeout_s), 20))

    raise ValueError(f"Unknown web search provider: {provider}")

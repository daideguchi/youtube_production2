from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

from factory_common.paths import repo_root


FACT_CHECK_REPORT_SCHEMA = "ytm.fact_check_report.v1"
# Bump this when extraction / verdict logic changes, so cached reports are recomputed.
FACT_CHECK_LOGIC_VERSION = "v2"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_text(text: str) -> str:
    h = hashlib.sha256()
    h.update((text or "").encode("utf-8", errors="ignore"))
    return h.hexdigest()


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


def _normalize_policy(value: str | None) -> str:
    raw = str(value or "").strip().lower().replace("-", "_")
    if raw in {"", "auto"}:
        return "auto"
    if raw in {"disabled", "disable", "off", "false", "0", "none", "no"}:
        return "disabled"
    if raw in {"required", "require", "enabled", "enable", "on", "true", "1", "yes"}:
        return "required"
    return "auto"


def _normalize_claim_status(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_")
    if raw in {"supported", "support", "true", "yes", "ok"}:
        return "supported"
    if raw in {"unsupported", "contradicted", "false", "no"}:
        return "unsupported"
    return "uncertain"


def _user_agent() -> str:
    return os.getenv("YTM_HTTP_USER_AGENT") or "factory_commentary/1.0 (+https://github.com/)"


def _read_json_optional(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class _Source:
    source_id: str
    url: str
    title: str | None = None
    snippet: str | None = None
    text: str | None = None


def _collect_sources(
    search_results: Dict[str, Any] | None,
    wikipedia_summary: Dict[str, Any] | None,
    references: Any,
    *,
    max_urls: int,
    fetch_timeout_s: int,
    fetch_max_chars: int,
) -> List[_Source]:
    sources: List[_Source] = []
    seen: set[str] = set()

    def _add(url: str, *, title: str | None = None, snippet: str | None = None) -> None:
        u = str(url or "").strip()
        if not (u.startswith("http://") or u.startswith("https://")):
            return
        if u in seen:
            return
        if len(seen) >= int(max_urls):
            return
        seen.add(u)
        sources.append(_Source(source_id=f"s{len(sources)+1}", url=u, title=title, snippet=snippet))

    # Prefer explicit references first (operator/LLM curated).
    if isinstance(references, list):
        for item in references:
            if not isinstance(item, dict):
                continue
            _add(str(item.get("url") or ""), title=str(item.get("title") or "").strip() or None)

    # Then web search hits.
    hits = (search_results or {}).get("hits") if isinstance(search_results, dict) else []
    if isinstance(hits, list):
        for item in hits:
            if not isinstance(item, dict):
                continue
            _add(
                str(item.get("url") or ""),
                title=str(item.get("title") or "").strip() or None,
                snippet=str(item.get("snippet") or "").strip() or None,
            )

    # Wikipedia page as one more source (extract is used as snippet; no fetch needed).
    if isinstance(wikipedia_summary, dict):
        url = str(wikipedia_summary.get("page_url") or "").strip()
        extract = str(wikipedia_summary.get("extract") or "").strip()
        if (
            url
            and url not in seen
            and (url.startswith("http://") or url.startswith("https://"))
            and len(seen) < int(max_urls)
        ):
            seen.add(url)
            sources.append(
                _Source(
                    source_id=f"s{len(sources)+1}",
                    url=url,
                    title=str(wikipedia_summary.get("page_title") or "").strip() or None,
                    snippet=extract[:800] if extract else None,
                    text=extract if extract else None,
                )
            )

    # Fetch missing texts.
    out: List[_Source] = []
    for s in sources:
        if s.text:
            out.append(s)
            continue
        fetched = _fetch_url_text(s.url, timeout_s=int(fetch_timeout_s), max_chars=int(fetch_max_chars))
        out.append(_Source(source_id=s.source_id, url=s.url, title=s.title, snippet=s.snippet, text=fetched))
    return out


def _fetch_url_text(url: str, *, timeout_s: int, max_chars: int) -> str | None:
    u = str(url or "").strip()
    if not u:
        return None
    headers = {
        "Accept": "text/html, text/plain;q=0.9, */*;q=0.1",
        "User-Agent": _user_agent(),
    }
    try:
        with requests.get(u, headers=headers, timeout=int(timeout_s), stream=True) as resp:
            if resp.status_code != 200:
                return None
            ctype = str(resp.headers.get("content-type") or "").lower()
            # Skip obvious binary formats (pdf/images) for now.
            if any(tok in ctype for tok in ("application/pdf", "image/", "application/zip")):
                return None
            max_bytes = max(1024, int(max_chars) * 4)
            chunks: List[bytes] = []
            size = 0
            for chunk in resp.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                chunks.append(chunk)
                size += len(chunk)
                if size >= max_bytes:
                    break
        raw = b"".join(chunks)
        encoding = resp.encoding or "utf-8"
        try:
            text = raw.decode(encoding, errors="ignore")
        except Exception:
            text = raw.decode("utf-8", errors="ignore")
        if "<html" in text.lower():
            text = _html_to_text(text)
        text = re.sub(r"[\\t\\r\\f\\v]+", " ", text)
        text = re.sub(r" {2,}", " ", text).strip()
        if not text:
            return None
        return text[: int(max_chars)]
    except Exception:
        return None


def _html_to_text(html: str) -> str:
    s = str(html or "")
    s = re.sub(r"(?is)<script\\b.*?>.*?</script>", " ", s)
    s = re.sub(r"(?is)<style\\b.*?>.*?</style>", " ", s)
    s = re.sub(r"(?is)<!--.*?-->", " ", s)
    s = re.sub(r"(?is)<noscript\\b.*?>.*?</noscript>", " ", s)
    # Very small/cheap tag stripping (best-effort).
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = s.replace("\\u00a0", " ")
    s = re.sub(r"[\\t\\r\\f\\v]+", " ", s)
    s = re.sub(r" {2,}", " ", s)
    s = re.sub(r"\\n{3,}", "\\n\\n", s)
    return s.strip()


def _split_sentences(text: str) -> List[str]:
    raw = str(text or "")
    raw = raw.replace("\\r\\n", "\\n").replace("\\r", "\\n")
    # Split on sentence-ending punctuation (Japanese often has no spaces) and newlines.
    # Avoid zero-length split patterns (can be brittle with re.split).
    chunks = [c.strip() for c in re.split(r"\\n+", raw) if c and c.strip()]
    parts: List[str] = []
    for chunk in chunks:
        for sent in re.split(r"(?<=[。！？!?])", chunk):
            s = sent.strip()
            if s:
                parts.append(s)
    return parts


def _claim_score(sentence: str) -> int:
    s = str(sentence or "")
    score = 0
    if re.search(r"[12][0-9]{3}年", s):
        score += 6
    if re.search(r"[%％]|パーセント", s):
        score += 6
    if re.search(r"[0-9０-９]", s):
        score += 4
    if re.search(r"(統計|研究|論文|調査|報告|データ|出典|引用|ソース|根拠)", s):
        score += 3
    if re.search(
        r"(ブッダ|仏陀|釈迦|如来|経典|スッタ|ダンマパダ|阿含|般若心経|法華経|浄土|涅槃|八正道|四諦|縁起)",
        s,
    ):
        score += 3
    if "「" in s or "『" in s:
        score += 1
    if re.search(r"(とされる|といわれる|と言われる|によると|に基づく)", s):
        score += 2
    return score


def extract_candidate_claims(a_text: str, *, max_claims: int, min_score: int = 4) -> List[Dict[str, str]]:
    sentences = _split_sentences(a_text)
    scored: List[Tuple[int, int, str]] = []
    for idx, sent in enumerate(sentences):
        if len(sent) < 12:
            continue
        score = _claim_score(sent)
        if score < int(min_score):
            continue
        scored.append((score, idx, sent))
    scored.sort(key=lambda t: (t[0], -t[1]), reverse=True)
    picked = scored[: max(0, int(max_claims))]
    out: List[Dict[str, str]] = []
    for i, (_score, _idx, sent) in enumerate(picked, start=1):
        out.append({"id": f"c{i}", "claim": sent, "context": sent})
    return out


def _keywords_for_claim(claim: str) -> List[str]:
    s = str(claim or "")
    tokens: List[str] = []
    for pat in (
        r"[12][0-9]{3}年",
        r"[0-9]{1,3}万",
        r"[0-9]{1,4}",
        r"[A-Za-z][A-Za-z0-9_-]{2,}",
        r"[ァ-ヶー]{3,}",
        r"[一-龠]{2,6}",
    ):
        for m in re.finditer(pat, s):
            tok = m.group(0)
            if tok and tok not in tokens:
                tokens.append(tok)
            if len(tokens) >= 8:
                break
        if len(tokens) >= 8:
            break
    return tokens


def _excerpt_for_claim(text: str | None, snippet: str | None, *, keywords: List[str], max_chars: int) -> str:
    base = (text or "").strip() or (snippet or "").strip()
    if not base:
        return ""
    base = base.replace("\\r\\n", "\\n")
    hit_at: Optional[int] = None
    hit_kw: Optional[str] = None
    for kw in keywords:
        if not kw:
            continue
        pos = base.find(kw)
        if pos >= 0:
            hit_at = pos
            hit_kw = kw
            break
    if hit_at is None:
        return base[: int(max_chars)]
    window = max(200, int(max_chars) // 2)
    start = max(0, hit_at - window)
    end = min(len(base), hit_at + len(hit_kw or "") + window)
    return base[start:end].strip()[: int(max_chars)]


def _build_codex_prompt(
    *,
    channel: str,
    video: str,
    claims: List[Dict[str, str]],
    evidence_by_claim: Dict[str, List[Dict[str, str]]],
) -> str:
    lines: List[str] = []
    lines.append("You are a strict fact-checking assistant.")
    lines.append("Rules (MANDATORY):")
    lines.append("- Use ONLY the EVIDENCE EXCERPTS provided for each claim below.")
    lines.append("- Do NOT use outside knowledge. Do NOT browse the web. Do NOT invent sources.")
    lines.append("- Do NOT invent quotes. Every quote MUST be an exact substring of the provided excerpt.")
    lines.append("- Output STRICT JSON ONLY. No markdown, no commentary.")
    lines.append("")
    lines.append(f"channel: {channel}")
    lines.append(f"video: {video}")
    lines.append("")
    lines.append("Return JSON with this shape (keys are required unless stated otherwise):")
    lines.append(
        json.dumps(
            {
                "schema": FACT_CHECK_REPORT_SCHEMA,
                "claims": [
                    {
                        "id": "c1",
                        "claim": "string",
                        "status": "supported|unsupported|uncertain",
                        "rationale": "short string",
                        "citations": [{"source_id": "s1", "url": "https://...", "quote": "exact substring"}],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    lines.append("")
    lines.append("Claims and evidence:")
    for c in claims:
        cid = c.get("id") or ""
        claim = c.get("claim") or ""
        context = c.get("context") or ""
        lines.append("")
        lines.append(f"[{cid}] CLAIM: {claim}")
        if context and context != claim:
            lines.append(f"[{cid}] CONTEXT: {context}")
        evs = evidence_by_claim.get(cid) or []
        if not evs:
            lines.append(f"[{cid}] EVIDENCE: (none)")
            continue
        lines.append(f"[{cid}] EVIDENCE_EXCERPTS:")
        for ev in evs:
            sid = ev.get("source_id") or ""
            url = ev.get("url") or ""
            excerpt = ev.get("excerpt") or ""
            title = ev.get("title") or ""
            if title:
                lines.append(f"- {sid}: {url} ({title})")
            else:
                lines.append(f"- {sid}: {url}")
            lines.append("  EXCERPT:")
            for ln in excerpt.splitlines():
                lines.append(f"    {ln}")
    lines.append("")
    return "\n".join(lines).strip() + "\n"


def _run_codex_fact_check(
    prompt: str,
    *,
    timeout_s: int,
    model: str | None = None,
) -> Tuple[Dict[str, Any] | None, Dict[str, Any]]:
    """
    Returns (json_obj_or_none, meta).
    """
    repo = repo_root()
    with tempfile.TemporaryDirectory(prefix="ytm_fact_check_") as td:
        out_path = Path(td) / "last_message.txt"
        cmd: List[str] = [
            "codex",
            "exec",
            "--sandbox",
            "read-only",
            "-C",
            str(repo),
            "--output-last-message",
            str(out_path),
        ]
        if model:
            cmd.extend(["-m", str(model)])
        cmd.append("-")  # read prompt from stdin
        try:
            subprocess.run(
                cmd,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=int(timeout_s),
                check=False,
            )
        except Exception as exc:
            return None, {"provider": "codex", "error": "codex_exec_failed", "exception": str(exc)}
        if not out_path.exists():
            return None, {"provider": "codex", "error": "codex_no_output"}
        try:
            text = out_path.read_text(encoding="utf-8")
        except Exception:
            text = ""
        obj = _extract_json_object(text)
        if not isinstance(obj, dict):
            return None, {"provider": "codex", "error": "codex_invalid_json"}
        return obj, {"provider": "codex", "model": model or None}


def _run_llm_router_fact_check(
    prompt: str,
    *,
    task: str,
    timeout_s: int,
    max_tokens: int,
) -> Tuple[Dict[str, Any] | None, Dict[str, Any]]:
    try:
        from factory_common.llm_router import get_router
    except Exception as exc:
        return None, {"provider": "llm_router", "error": "llm_router_unavailable", "exception": str(exc)}
    router = get_router()
    try:
        result = router.call_with_raw(
            task=task,
            messages=[
                {"role": "system", "content": "You are a strict fact checker. Output JSON only."},
                {"role": "user", "content": prompt},
            ],
            timeout=int(timeout_s),
            max_tokens=int(max_tokens),
            response_format="json_object",
        )
    except Exception as exc:
        return None, {"provider": "llm_router", "error": "llm_router_call_failed", "exception": str(exc)}
    content = result.get("content")
    if isinstance(content, list):
        text = " ".join(str(part.get("text", "")).strip() for part in content if isinstance(part, dict)).strip()
    else:
        text = str(content or "").strip()
    obj = _extract_json_object(text)
    if not isinstance(obj, dict):
        return None, {
            "provider": f"llm_router:{result.get('provider') or 'unknown'}:{result.get('model') or 'unknown'}",
            "error": "llm_router_invalid_json",
        }
    return obj, {
        "provider": f"llm_router:{result.get('provider') or 'unknown'}:{result.get('model') or 'unknown'}",
        "request_id": result.get("request_id"),
        "usage": result.get("usage") or {},
    }


def _deterministic_verdict(claim_statuses: Iterable[str]) -> str:
    statuses = [str(s or "").strip().lower() for s in claim_statuses]
    if any(s == "unsupported" for s in statuses):
        return "fail"
    if any(s == "uncertain" for s in statuses):
        return "warn"
    return "pass"


def run_fact_check_with_codex(
    *,
    channel: str,
    video: str,
    a_text: str,
    policy: str,
    search_results_path: Path,
    wikipedia_summary_path: Path,
    references_path: Path,
    output_path: Path,
) -> Dict[str, Any]:
    """
    Create/refresh `fact_check_report.json` for the final A-text.

    The report is evidence-based:
    - Claims are checked ONLY against `search_results.json`, `wikipedia_summary.json`, and `references.json`.
    - The checker must not invent URLs or quotes.
    - This function never edits A-text.
    """
    policy_norm = _normalize_policy(policy)
    max_claims = int(os.getenv("YTM_FACT_CHECK_MAX_CLAIMS") or 12)
    max_urls = int(os.getenv("YTM_FACT_CHECK_MAX_URLS") or 8)
    max_sources_per_claim = int(os.getenv("YTM_FACT_CHECK_MAX_SOURCES_PER_CLAIM") or 2)
    try:
        min_claim_score = int(os.getenv("YTM_FACT_CHECK_MIN_CLAIM_SCORE") or 4)
    except Exception:
        min_claim_score = 4
    fetch_timeout_s = int(os.getenv("YTM_FACT_CHECK_FETCH_TIMEOUT_S") or 20)
    fetch_max_chars = int(os.getenv("YTM_FACT_CHECK_FETCH_MAX_CHARS") or 20000)
    excerpt_max_chars = int(os.getenv("YTM_FACT_CHECK_EXCERPT_MAX_CHARS") or 1400)
    codex_timeout_s = int(os.getenv("YTM_FACT_CHECK_CODEX_TIMEOUT_S") or 180)
    codex_model = (os.getenv("YTM_FACT_CHECK_CODEX_MODEL") or "").strip() or None
    force = os.getenv("YTM_FACT_CHECK_FORCE", "0") == "1"
    allow_llm_fallback = os.getenv("YTM_FACT_CHECK_LLM_FALLBACK", "1") != "0"
    llm_task = (os.getenv("YTM_FACT_CHECK_LLM_TASK") or "script_a_text_quality_judge").strip()
    llm_timeout_s = int(os.getenv("YTM_FACT_CHECK_LLM_TIMEOUT_S") or 120)
    llm_max_tokens = int(os.getenv("YTM_FACT_CHECK_LLM_MAX_TOKENS") or 2000)

    fingerprint = _sha256_text(a_text)
    existing = _read_json_optional(output_path)
    if (
        not force
        and isinstance(existing, dict)
        and existing.get("schema") == FACT_CHECK_REPORT_SCHEMA
        and str(existing.get("logic_version") or "") == FACT_CHECK_LOGIC_VERSION
        and str(existing.get("input_fingerprint") or "") == fingerprint
        and str(existing.get("policy") or "") == policy_norm
    ):
        return existing

    if policy_norm == "disabled" or os.getenv("SCRIPT_PIPELINE_DRY", "0") == "1":
        report = {
            "schema": FACT_CHECK_REPORT_SCHEMA,
            "logic_version": FACT_CHECK_LOGIC_VERSION,
            "generated_at": _utc_now_iso(),
            "provider": "disabled",
            "policy": "disabled",
            "verdict": "skipped",
            "channel": str(channel),
            "video": str(video),
            "input_fingerprint": fingerprint,
            "claims": [],
        }
        _write_json(output_path, report)
        return report

    claims = extract_candidate_claims(a_text, max_claims=max_claims, min_score=min_claim_score)
    if not claims:
        report = {
            "schema": FACT_CHECK_REPORT_SCHEMA,
            "logic_version": FACT_CHECK_LOGIC_VERSION,
            "generated_at": _utc_now_iso(),
            "provider": "no_checkable_claims",
            "policy": policy_norm,
            "verdict": "pass",
            "channel": str(channel),
            "video": str(video),
            "input_fingerprint": fingerprint,
            "note": "no_checkable_claims",
            "claims": [],
        }
        _write_json(output_path, report)
        return report

    search_results = _read_json_optional(search_results_path)
    wikipedia_summary = _read_json_optional(wikipedia_summary_path)
    references = _read_json_optional(references_path)

    sources = _collect_sources(
        search_results if isinstance(search_results, dict) else None,
        wikipedia_summary if isinstance(wikipedia_summary, dict) else None,
        references,
        max_urls=max_urls,
        fetch_timeout_s=fetch_timeout_s,
        fetch_max_chars=fetch_max_chars,
    )

    evidence_by_claim: Dict[str, List[Dict[str, str]]] = {}
    excerpt_lookup: Dict[Tuple[str, str], str] = {}
    for c in claims:
        cid = c.get("id") or ""
        kws = _keywords_for_claim(c.get("claim") or "")
        evs: List[Dict[str, str]] = []
        for s in sources:
            if len(evs) >= int(max_sources_per_claim):
                break
            excerpt = _excerpt_for_claim(s.text, s.snippet, keywords=kws, max_chars=excerpt_max_chars)
            if not excerpt:
                continue
            evs.append(
                {
                    "source_id": s.source_id,
                    "url": s.url,
                    "title": (s.title or ""),
                    "excerpt": excerpt,
                }
            )
            excerpt_lookup[(cid, s.source_id)] = excerpt
        evidence_by_claim[cid] = evs

    prompt = _build_codex_prompt(channel=channel, video=video, claims=claims, evidence_by_claim=evidence_by_claim)
    obj, meta = _run_codex_fact_check(prompt, timeout_s=codex_timeout_s, model=codex_model)
    if obj is None and allow_llm_fallback:
        obj, meta2 = _run_llm_router_fact_check(
            prompt,
            task=llm_task,
            timeout_s=llm_timeout_s,
            max_tokens=llm_max_tokens,
        )
        meta = {**(meta or {}), **(meta2 or {})}

    out_claims: List[Dict[str, Any]] = []
    out_by_id: Dict[str, Dict[str, Any]] = {}
    raw_claims = (obj or {}).get("claims") if isinstance(obj, dict) else None
    if isinstance(raw_claims, list):
        for item in raw_claims:
            if not isinstance(item, dict):
                continue
            cid = str(item.get("id") or "").strip()
            if cid:
                out_by_id[cid] = item

    statuses: List[str] = []
    for c in claims:
        cid = c.get("id") or ""
        base_claim = c.get("claim") or ""
        raw = out_by_id.get(cid) or {}
        status = _normalize_claim_status(raw.get("status"))
        rationale = str(raw.get("rationale") or "").strip()
        citations_in = raw.get("citations") or []
        citations_out: List[Dict[str, str]] = []
        if isinstance(citations_in, list):
            for it in citations_in:
                if not isinstance(it, dict):
                    continue
                sid = str(it.get("source_id") or "").strip()
                url = str(it.get("url") or "").strip()
                quote = str(it.get("quote") or "").strip()
                excerpt = excerpt_lookup.get((cid, sid), "")
                # Enforce: quote must be from the provided excerpt.
                if not (sid and excerpt and quote and quote in excerpt):
                    continue
                citations_out.append({"source_id": sid, "url": url, "quote": quote})
                if len(citations_out) >= 3:
                    break
        if not citations_out and status == "supported":
            # Supported without valid citations is not acceptable.
            status = "uncertain"
            if not rationale:
                rationale = "supported_without_citations"
        statuses.append(status)
        out_claims.append(
            {
                "id": cid,
                "claim": base_claim,
                "status": status,
                "rationale": rationale or None,
                "citations": citations_out,
            }
        )

    verdict = _deterministic_verdict(statuses)
    report: Dict[str, Any] = {
        "schema": FACT_CHECK_REPORT_SCHEMA,
        "logic_version": FACT_CHECK_LOGIC_VERSION,
        "generated_at": _utc_now_iso(),
        "provider": (meta or {}).get("provider") or "codex",
        "policy": policy_norm,
        "verdict": verdict,
        "channel": str(channel),
        "video": str(video),
        "input_fingerprint": fingerprint,
        "claims": out_claims,
    }
    if obj is None:
        report["verdict"] = "fail" if policy_norm == "required" else "warn"
        report["error"] = (meta or {}).get("error") or "fact_check_unavailable"
        report["exception"] = (meta or {}).get("exception") or None
    _write_json(output_path, report)
    return report

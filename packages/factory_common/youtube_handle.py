from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Set
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_CHANNEL_ID_PATTERN = re.compile(r"^UC[a-zA-Z0-9_-]{22}$")
_BROWSE_ID_RE = re.compile(r"\"browseId\":\"(UC[a-zA-Z0-9_-]{22})\"")
_CHANNEL_ID_RE = re.compile(r"\"channelId\":\"(UC[a-zA-Z0-9_-]{22})\"")
_EXTERNAL_ID_RE = re.compile(r"\"externalId\":\"(UC[a-zA-Z0-9_-]{22})\"")
_OG_URL_RE = re.compile(
    r"property=[\"']og:url[\"'][^>]*content=[\"']https://www\.youtube\.com/channel/(UC[a-zA-Z0-9_-]{22})(?:/)?[\"']",
    re.IGNORECASE,
)
_CANONICAL_CHANNEL_RE = re.compile(r"https://www\.youtube\.com/channel/(UC[a-zA-Z0-9_-]{22})")


class YouTubeHandleResolutionError(RuntimeError):
    pass


def normalize_youtube_handle(value: str) -> str:
    """
    Normalize input into a YouTube handle in '@name' form.

    Accepts:
    - '@name'
    - 'name'
    - 'https://www.youtube.com/@name'

    Rejects channel URLs because they are not handles.
    """

    raw = (value or "").strip()
    if not raw:
        raise ValueError("empty handle")

    lowered = raw.lower()
    if "youtube.com" in lowered and "/channel/" in lowered:
        raise ValueError("channel URL is not a handle")

    if "youtube.com" in lowered and "/@" in lowered:
        try:
            after = raw.split("@", 1)[1]
        except Exception as exc:  # pragma: no cover
            raise ValueError("invalid handle url") from exc
        token = after.split("/", 1)[0].split("?", 1)[0].strip()
        if not token:
            raise ValueError("invalid handle url")
        return "@" + token.lstrip("@")

    token = raw[1:].strip() if raw.startswith("@") else raw.strip()
    token = token.lstrip("@").strip()
    if not token:
        raise ValueError("invalid handle")
    return "@" + token


def find_channel_ids_in_youtube_html(html: str) -> Set[str]:
    """
    Extract UC... channel IDs from a YouTube HTML page.
    Returns a deduped set (may be empty).
    """

    text = html or ""
    ids = (
        set(_BROWSE_ID_RE.findall(text))
        | set(_CHANNEL_ID_RE.findall(text))
        | set(_EXTERNAL_ID_RE.findall(text))
        | set(_OG_URL_RE.findall(text))
        | set(_CANONICAL_CHANNEL_RE.findall(text))
    )
    return {cid for cid in ids if _CHANNEL_ID_PATTERN.match(cid)}


@dataclass(frozen=True)
class YouTubeHandleResolution:
    handle: str
    channel_id: str
    url: str


def resolve_youtube_channel_id_from_handle(handle: str, *, timeout_sec: int = 20) -> YouTubeHandleResolution:
    """
    Deterministic handle -> channelId resolver.

    This intentionally avoids YouTube Data API search (ambiguous) and instead
    resolves via the canonical handle page: https://www.youtube.com/@handle .
    """

    normalized = normalize_youtube_handle(handle)
    handle_url = f"https://www.youtube.com/{normalized}?app=desktop"
    req = Request(
        handle_url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            ),
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        },
    )

    try:
        with urlopen(req, timeout=timeout_sec) as resp:  # nosec B310
            html = resp.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raise YouTubeHandleResolutionError(f"HTTP {exc.code} for {handle_url}") from exc
    except URLError as exc:
        raise YouTubeHandleResolutionError(f"Network error for {handle_url}: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise YouTubeHandleResolutionError(f"Unexpected error for {handle_url}: {exc}") from exc

    og_ids = set(_OG_URL_RE.findall(html))
    if len(og_ids) == 1:
        channel_id = next(iter(og_ids))
        return YouTubeHandleResolution(
            handle=normalized,
            channel_id=channel_id,
            url=f"https://www.youtube.com/channel/{channel_id}",
        )
    if len(og_ids) > 1:
        raise YouTubeHandleResolutionError(f"og:url resolved to multiple channel IDs: {normalized} -> {sorted(og_ids)}")

    external_ids = set(_EXTERNAL_ID_RE.findall(html))
    if len(external_ids) == 1:
        channel_id = next(iter(external_ids))
        return YouTubeHandleResolution(
            handle=normalized,
            channel_id=channel_id,
            url=f"https://www.youtube.com/channel/{channel_id}",
        )
    if len(external_ids) > 1:
        raise YouTubeHandleResolutionError(
            f"externalId resolved to multiple channel IDs: {normalized} -> {sorted(external_ids)}"
        )

    ids = find_channel_ids_in_youtube_html(html)
    if not ids:
        raise YouTubeHandleResolutionError(f"Could not resolve channel_id from handle page: {normalized}")
    if len(ids) > 1:
        raise YouTubeHandleResolutionError(f"Handle resolved to multiple channel IDs: {normalized} -> {sorted(ids)}")
    channel_id = next(iter(ids))
    return YouTubeHandleResolution(
        handle=normalized,
        channel_id=channel_id,
        url=f"https://www.youtube.com/channel/{channel_id}",
    )


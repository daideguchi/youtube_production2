from __future__ import annotations

from typing import Any, Dict

# requests is optional; fall back to urllib when not installed.
try:
    import requests  # type: ignore
except Exception:  # pragma: no cover
    requests = None  # type: ignore
    import json
    import urllib.parse
    import urllib.request


class VoicevoxClient:
    """Minimal VOICEVOX HTTP client (audio_query + synthesis)."""

    def __init__(self, engine_url: str = "http://127.0.0.1:50021") -> None:
        self.base = engine_url.rstrip("/")

    def audio_query(self, text: str, speaker: int) -> Dict[str, Any]:
        url = f"{self.base}/audio_query"
        if requests is not None:
            resp = requests.post(url, params={"text": text, "speaker": speaker})
            resp.raise_for_status()
            return resp.json()
        params = urllib.parse.urlencode({"text": text, "speaker": speaker})
        req = urllib.request.Request(f"{url}?{params}", method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310
            if r.status != 200:
                raise RuntimeError(f"VOICEVOX audio_query failed: {r.status}")
            return json.loads(r.read().decode("utf-8"))

    def get_kana(self, text: str, speaker: int) -> str:
        """Return kana string from audio_query response."""
        query = self.audio_query(text, speaker)
        return str(query.get("kana") or "")

    def synthesis(self, audio_query: Dict[str, Any], speaker: int) -> bytes:
        url = f"{self.base}/synthesis"
        if requests is not None:
            resp = requests.post(url, params={"speaker": speaker}, json=audio_query)
            resp.raise_for_status()
            return resp.content
        params = urllib.parse.urlencode({"speaker": speaker})
        body = json.dumps(audio_query, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(f"{url}?{params}", data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
            if r.status != 200:
                raise RuntimeError(f"VOICEVOX synthesis failed: {r.status}")
            return r.read()

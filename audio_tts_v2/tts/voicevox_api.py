from __future__ import annotations

import requests
from typing import Any, Dict


class VoicevoxClient:
    """Minimal VOICEVOX HTTP client (audio_query + synthesis)."""

    def __init__(self, engine_url: str = "http://127.0.0.1:50021") -> None:
        self.base = engine_url.rstrip("/")

    def audio_query(self, text: str, speaker: int) -> Dict[str, Any]:
        url = f"{self.base}/audio_query"
        resp = requests.post(url, params={"text": text, "speaker": speaker})
        resp.raise_for_status()
        return resp.json()

    def get_kana(self, text: str, speaker: int) -> str:
        """Return kana string from audio_query response."""
        query = self.audio_query(text, speaker)
        return str(query.get("kana") or "")

    def synthesis(self, audio_query: Dict[str, Any], speaker: int) -> bytes:
        url = f"{self.base}/synthesis"
        resp = requests.post(url, params={"speaker": speaker}, json=audio_query)
        resp.raise_for_status()
        return resp.content

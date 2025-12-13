from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class UserDictWord:
    uuid: str
    surface: str
    pronunciation: str
    accent_type: int


class VoicevoxUserDictClient:
    """Client for VOICEVOX ENGINE official user dictionary endpoints.

    Official routes (VOICEVOX/voicevox_engine README):
      - GET    /user_dict
      - POST   /user_dict_word?surface=...&pronunciation=...&accent_type=...
      - PUT    /user_dict_word/{uuid}?surface=...&pronunciation=...&accent_type=...
      - DELETE /user_dict_word/{uuid}
    """

    def __init__(self, engine_url: str = "http://127.0.0.1:50021") -> None:
        self.base = engine_url.rstrip("/")

    def _request_json(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.base}{path}"
        if params:
            qs = urllib.parse.urlencode(params)
            url = f"{url}?{qs}"
        req = urllib.request.Request(url, method=method)
        with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310
            body = r.read().decode("utf-8")
            if not body:
                return None
            return json.loads(body)

    def list_words(self) -> Dict[str, Dict[str, Any]]:
        """Return mapping {uuid: word_payload}."""
        payload = self._request_json("GET", "/user_dict") or {}
        if not isinstance(payload, dict):
            return {}
        return {str(k): v for k, v in payload.items() if isinstance(v, dict)}

    def add_word(self, surface: str, pronunciation: str, accent_type: int) -> str:
        """Add a word; returns word_uuid."""
        params = {
            "surface": surface,
            "pronunciation": pronunciation,
            "accent_type": int(accent_type),
        }
        res = self._request_json("POST", "/user_dict_word", params=params)
        if isinstance(res, str) and res:
            return res
        # Engine may return JSON with uuid field
        if isinstance(res, dict):
            uuid = res.get("uuid") or res.get("word_uuid")
            if isinstance(uuid, str) and uuid:
                return uuid
        raise RuntimeError(f"VOICEVOX user_dict_word add failed for surface='{surface}'")

    def update_word(self, word_uuid: str, surface: str, pronunciation: str, accent_type: int) -> None:
        params = {
            "surface": surface,
            "pronunciation": pronunciation,
            "accent_type": int(accent_type),
        }
        self._request_json("PUT", f"/user_dict_word/{word_uuid}", params=params)

    def delete_word(self, word_uuid: str) -> None:
        self._request_json("DELETE", f"/user_dict_word/{word_uuid}")


from __future__ import annotations

from typing import Dict, List, Optional

from .routing import load_routing_config, resolve_voicevox_speaker_id, RoutingConfig, voicepeak_defaults
from .voicevox_api import VoicevoxClient


def normalize_kana(text: str) -> str:
    to_remove = "'/、 \t\r\n"
    return (text or "").translate(str.maketrans("", "", to_remove))


def _voicevox_kana(text: str, channel: str, cfg: Optional[RoutingConfig] = None) -> str:
    """VOICEVOX kana 取得（長文は分割して再試行）。"""
    import re

    def _split(t: str, limit: int = 80) -> List[str]:
        t = t.strip()
        if len(t) <= limit:
            return [t] if t else []
        parts = re.split(r"(?<=[。？！\?！\!])", t)
        chunks: List[str] = []
        buf = ""
        for p in parts:
            s = p.strip()
            if not s:
                continue
            if len(buf) + len(s) <= limit:
                buf += s
            else:
                if buf:
                    chunks.append(buf)
                if len(s) <= limit:
                    buf = s
                else:
                    step = max(40, limit // 2)
                    for i in range(0, len(s), step):
                        chunks.append(s[i : i + step])
                    buf = ""
        if buf:
            chunks.append(buf)
        return [c for c in chunks if c]

    cfg = cfg or load_routing_config()
    speaker_id = resolve_voicevox_speaker_id(channel, cfg)
    client = VoicevoxClient(engine_url=cfg.voicevox_url)
    try:
        return client.get_kana(text, speaker_id)
    except Exception:
        parts = []
        for chunk in _split(text):
            try:
                k = client.get_kana(chunk, speaker_id)
                if k:
                    parts.append(k)
            except Exception:
                continue
        if parts:
            return " ".join(parts)
        raise


def _mecab_concat(tokens: List[Dict[str, object]]) -> str:
    parts: List[str] = []
    for t in tokens:
        parts.append(str(t.get("reading_mecab") or t.get("surface") or ""))
    return "".join(parts)


def build_kana_engine(engine: str, a_text: str, tokens: Optional[List[Dict[str, object]]] = None, reading_source: Optional[str] = None, cfg: Optional[RoutingConfig] = None, channel: str = "") -> Dict[str, object]:
    cfg = cfg or load_routing_config()
    raw = ""
    source = reading_source
    if engine == "voicevox":
        raw = _voicevox_kana(a_text, channel, cfg)
        source = "voicevox"
    elif engine == "voicepeak":
        defaults = voicepeak_defaults(channel, cfg)
        src = (reading_source or defaults.get("reading_source") or "mecab").lower()
        if src == "voicevox":
            raw = _voicevox_kana(a_text, channel, cfg)
        else:
            raw = _mecab_concat(tokens or [])
        source = src
    elif engine == "elevenlabs":
        raw = _mecab_concat(tokens or [])
        source = "mecab"
    else:
        raise ValueError(f"Unknown engine: {engine}")

    return {
        "raw": raw,
        "normalized": normalize_kana(raw),
        "reading_source": source,
        "reading_mecab_joined": _mecab_concat(tokens or []),
    }

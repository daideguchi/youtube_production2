from __future__ import annotations

import os

import requests
from fastapi import APIRouter

from audio_tts.tts.routing import load_routing_config, resolve_eleven_model, resolve_eleven_voice

router = APIRouter(prefix="/api/audio-tts", tags=["audio-tts"])


@router.get("/health")
def audio_tts_health():
    try:
        cfg = load_routing_config()
    except Exception as exc:
        return {"status": "error", "detail": f"routing_config_load_failed: {exc}"}

    result = {
        "status": "ok",
        "engine_default": getattr(cfg, "engine_default", None),
        "engine_override_env": os.getenv("ENGINE_DEFAULT_OVERRIDE"),
        "voicevox": {
            "url": getattr(cfg, "voicevox_url", None),
            "speaker_env": getattr(cfg, "voicevox_speaker_env", None),
            "ok": False,
            "detail": None,
        },
        "azure_openai": {
            "api_key_present": bool(os.getenv("AZURE_OPENAI_API_KEY")),
            "endpoint": os.getenv("AZURE_OPENAI_ENDPOINT"),
            "deployment": os.getenv("AZURE_OPENAI_DEPLOYMENT"),
        },
        "elevenlabs": {
            "api_key_present": bool(os.getenv(getattr(cfg, "eleven_api_key_env", ""))),
            "voice_id": resolve_eleven_voice("", cfg=cfg) if getattr(cfg, "eleven_voice_id", None) else None,
            "model_id": resolve_eleven_model(cfg) if cfg else None,
        },
    }
    # Voicevox ping (best effort)
    try:
        if getattr(cfg, "voicevox_url", None):
            resp = requests.get(f"{cfg.voicevox_url}/speakers", timeout=2)
            resp.raise_for_status()
            result["voicevox"]["ok"] = True
    except Exception as exc:  # pragma: no cover - best effort check
        result["voicevox"]["detail"] = str(exc)
    return result


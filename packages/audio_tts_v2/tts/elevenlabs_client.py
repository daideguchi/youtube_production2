from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

import requests


class ElevenLabsClient:
    def __init__(self, api_key: str, voice_id: str, model_id: str = "eleven_multilingual_v2", base: str = "https://api.elevenlabs.io") -> None:
        self.api_key = api_key
        self.voice_id = voice_id
        self.model_id = model_id
        self.base = base.rstrip("/")

    def text_to_speech(self, text: str, output_path: Path, format: str = "wav") -> Dict[str, object]:
        """
        Standard HTTPS Text to Speech (non-streaming). Model defaults to config (v3/turbo if set).
        output_formatはクエリパラメータで指定（公式仕様）。
        """
        if not text.strip():
            raise ValueError("text is empty")
        output_fmt = "pcm_24000" if format == "wav" else "mp3_44100_128"
        url = f"{self.base}/v1/text-to-speech/{self.voice_id}?output_format={output_fmt}"
        headers = {"xi-api-key": self.api_key, "Content-Type": "application/json"}
        payload = {
            "text": text,
            "model_id": self.model_id,
            # Stability must be one of {0.0, 0.5, 1.0} per error; use 0.5 as default.
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.5},
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        try:
            resp.raise_for_status()
        except requests.HTTPError as e:
            raise RuntimeError(f"ElevenLabs TTS error {resp.status_code}: {resp.text}") from e
        output_path.parent.mkdir(parents=True, exist_ok=True)
        data = resp.content
        duration = 0.0
        if format == "wav":
            import wave

            # ElevenLabs pcm_24000 は 16bit PCM (little-endian) mono として扱う
            with wave.open(str(output_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(24000)
                wf.writeframes(data)
                duration = wf.getnframes() / float(wf.getframerate())
        else:
            output_path.write_bytes(data)
            try:
                import wave

                with wave.open(str(output_path), "rb") as wf:
                    duration = wf.getnframes() / float(wf.getframerate())
            except Exception:
                pass
        return {
            "wav_path": str(output_path),
            "sample_rate": 24000 if format == "wav" else None,
            "duration_sec": duration,
            "character_count": resp.headers.get("x-character-count"),
        }


def build_eleven_client(api_key_env: str, voice_id: str, model_id: str) -> ElevenLabsClient:
    key = os.getenv(api_key_env)
    if not key:
        raise RuntimeError(f"ElevenLabs API key missing (env {api_key_env})")
    if not voice_id:
        raise RuntimeError("ElevenLabs voice_id not configured")
    return ElevenLabsClient(api_key=key, voice_id=voice_id, model_id=model_id)

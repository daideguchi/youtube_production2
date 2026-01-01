from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from factory_common.paths import audio_pkg_root, script_pkg_root

CONFIG_PATH = audio_pkg_root() / "configs" / "routing.json"


@dataclass
class RoutingConfig:
    engine_default: str
    channel_override: Dict[str, str]
    script_override: Dict[str, str]
    voicevox_url: str
    voicevox_speaker_env: str
    voicevox_channel_speaker_env: Dict[str, str]
    voicepeak_binary_path: str
    voicepeak_default_narrator: str
    voicepeak_default_speed: int
    voicepeak_default_pitch: int
    voicepeak_default_emotion: str
    voicepeak_reading_source: str
    voicepeak_channel_narrator: Dict[str, str]
    llm_model: str
    eleven_api_key_env: str
    eleven_voice_id: str
    eleven_voice_id_env: Optional[str]
    eleven_model_id: str
    eleven_model_id_env: Optional[str]
    eleven_channel_voice_id: Dict[str, str]
    eleven_voice_alias: Dict[str, str]


def load_routing_config(path: Path = CONFIG_PATH) -> RoutingConfig:
    data = json.loads(path.read_text(encoding="utf-8"))
    vv = data["voicevox"]
    vp = data["voicepeak"]
    llm = data.get("llm", {})
    el = data.get("elevenlabs", {})
    return RoutingConfig(
        engine_default=data.get("engine_default", "voicevox"),
        channel_override=data.get("channel_override", {}),
        script_override=data.get("script_override", {}),
        voicevox_url=vv["url"],
        voicevox_speaker_env=vv["speaker_env"],
        voicevox_channel_speaker_env=vv.get("channel_speaker_env", {}),
        voicepeak_binary_path=vp["binary_path"],
        voicepeak_default_narrator=vp["default_narrator"],
        voicepeak_default_speed=vp.get("default_speed", 100),
        voicepeak_default_pitch=vp.get("default_pitch", 0),
        voicepeak_default_emotion=vp.get("default_emotion", ""),
        voicepeak_reading_source=vp.get("reading_source", "mecab"),
        voicepeak_channel_narrator=vp.get("channel_narrator", {}),
        llm_model=llm.get("model", "gpt-5-mini"),
        eleven_api_key_env=el.get("api_key_env", "ELEVENLABS_API_KEY"),
        eleven_voice_id=el.get("voice_id", ""),
        eleven_voice_id_env=el.get("voice_id_env"),
        eleven_model_id=el.get("model_id", "eleven_multilingual_v2"),
        eleven_model_id_env=el.get("model_id_env"),
        eleven_channel_voice_id=el.get("channel_voice_id", {}),
        eleven_voice_alias=el.get("voice_alias", {}),
    )


def load_default_voice_config(channel: str) -> Optional[Dict[str, Any]]:
    """
    Load script_pipeline's per-channel `voice_config.json` and return the default voice entry.

    This is the SSOT for:
      - which engine the channel uses by default (voicevox/voicepeak/...)
      - engine-specific options (narrator/pitch/speed/etc.)
    """
    config_path = script_pkg_root() / "audio" / "channels" / channel / "voice_config.json"
    if not config_path.exists():
        return None
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    if not isinstance(data, dict):
        return None
    key = data.get("default_voice_key")
    voices = data.get("voices")
    if not (isinstance(key, str) and isinstance(voices, dict)):
        return None
    entry = voices.get(key)
    return entry if isinstance(entry, dict) else None


def decide_engine(channel: str, video_no: str, cfg: Optional[RoutingConfig] = None) -> str:
    cfg = cfg or load_routing_config()
    env_override = os.getenv("ENGINE_DEFAULT_OVERRIDE")
    if env_override:
        return env_override.strip().lower()
    key = f"{channel}-{video_no}"
    if key in cfg.script_override:
        return str(cfg.script_override[key]).strip().lower()

    voice_cfg = load_default_voice_config(channel)
    if isinstance(voice_cfg, dict) and voice_cfg.get("engine"):
        return str(voice_cfg["engine"]).strip().lower()

    if channel in cfg.channel_override:
        return str(cfg.channel_override[channel]).strip().lower()
    return str(cfg.engine_default).strip().lower()


def resolve_voicevox_speaker_id(channel: Optional[str] = None, cfg: Optional[RoutingConfig] = None) -> int:
    # allow resolve_voicevox_speaker_id(cfg) style invocation for tests
    if cfg is None and channel is not None and isinstance(channel, RoutingConfig):
        cfg = channel
        channel = None
    cfg = cfg or load_routing_config()
    env_name = cfg.voicevox_channel_speaker_env.get(channel, cfg.voicevox_speaker_env) if channel else cfg.voicevox_speaker_env
    value = os.getenv(env_name)
    if not value:
        raise ValueError(f"Env {env_name} is required for VOICEVOX speaker_id (no fallback allowed)")
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Env {env_name} must be integer") from exc


def voicepeak_defaults(channel: str, cfg: Optional[RoutingConfig] = None) -> Dict[str, object]:
    cfg = cfg or load_routing_config()
    narrator = cfg.voicepeak_channel_narrator.get(channel, cfg.voicepeak_default_narrator)
    return {
        "binary_path": cfg.voicepeak_binary_path,
        "narrator": narrator,
        "speed": cfg.voicepeak_default_speed,
        "pitch": cfg.voicepeak_default_pitch,
        "emotion": cfg.voicepeak_default_emotion,
        "reading_source": cfg.voicepeak_reading_source,
    }


def resolve_eleven_voice(channel: str, cfg: Optional[RoutingConfig] = None) -> str:
    cfg = cfg or load_routing_config()
    if channel in cfg.eleven_channel_voice_id:
        return cfg.eleven_channel_voice_id[channel]
    env = cfg.eleven_voice_id_env
    if env and os.getenv(env):
        return os.getenv(env)  # type: ignore
    return cfg.eleven_voice_id


def resolve_eleven_model(cfg: Optional[RoutingConfig] = None) -> str:
    cfg = cfg or load_routing_config()
    env = cfg.eleven_model_id_env
    if env and os.getenv(env):
        return os.getenv(env)  # type: ignore
    return cfg.eleven_model_id

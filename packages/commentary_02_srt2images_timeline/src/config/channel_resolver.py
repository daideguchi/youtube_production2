#!/usr/bin/env python3
"""Channel preset resolver for dynamic per-channel configuration."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional, Any

# Import new domain schema
from core.domain.channel_schema import ChannelRegistry, ChannelConfig
from config.template_registry import is_registered_template, resolve_template_path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PRESET_PATH = PROJECT_ROOT / "config" / "channel_presets.json"

CHANNEL_PATTERN = re.compile(r"CH\d{2}")


@dataclass
class ChannelPreset:
    """Legacy wrapper for backward compatibility with existing tools."""
    channel_id: str
    name: str
    prompt_template: Optional[str] = None
    prompt_suffix: Optional[str] = None
    tone_profile: Optional[str] = None
    character_note: Optional[str] = None
    style: Optional[str] = None
    capcut_template: Optional[str] = None
    position: Dict[str, float] = field(default_factory=lambda: {"tx": 0.0, "ty": 0.0, "scale": 1.0})
    belt: Dict[str, Any] = field(default_factory=dict)
    capcut_settings: Dict[str, Any] = field(default_factory=dict)
    persona_required: bool = False
    notes: str = ""
    status: str = "active"
    
    # Reference to the validated Pydantic model
    config_model: Optional[ChannelConfig] = None

    def resolved_prompt_template(self) -> Optional[str]:
        resolved = _resolve_path(self.prompt_template)
        if resolved and not is_registered_template(resolved):
            logger.warning("Template not registered: %s (channel=%s)", resolved, self.channel_id)
        return resolved


def _resolve_path(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    # Allow registry-relative paths
    if Path(value).name == value:
        path = resolve_template_path(value)
    else:
        path = Path(value)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
    return str(path)


class ChannelPresetResolver:
    """Loads channel presets and resolves them by channel id."""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = Path(config_path or DEFAULT_PRESET_PATH)

    @lru_cache(maxsize=1)
    def _load_config(self) -> ChannelRegistry:
        if not self.config_path.exists():
            logger.warning("Channel preset config not found: %s", self.config_path)
            return ChannelRegistry(channels={})
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
                # Validate using Pydantic
                return ChannelRegistry(**raw_data)
        except Exception as exc:
            logger.error("Failed to parse channel preset config: %s", exc)
            return ChannelRegistry(channels={})

    def get_channels(self) -> Dict[str, ChannelPreset]:
        registry = self._load_config()
        channels = {}
        for channel_id, config in registry.channels.items():
            # Map Pydantic model to legacy dataclass
            channels[channel_id] = ChannelPreset(
                channel_id=channel_id,
                name=config.name,
                prompt_template=config.prompt_template,
                prompt_suffix=getattr(config, "prompt_suffix", None),
                tone_profile=getattr(config, "tone_profile", None),
                character_note=getattr(config, "character_note", None),
                style=config.style,
                capcut_template=config.capcut_template,
                position=config.position.model_dump(),
                belt=config.belt.model_dump(),
                capcut_settings=config.capcut_settings.model_dump() if config.capcut_settings else {},
                persona_required=config.persona_required,
                notes=config.notes,
                status=config.status,
                config_model=config
            )
        return channels

    def resolve(self, channel_id: Optional[str]) -> Optional[ChannelPreset]:
        if not channel_id:
            return None
        channels = self.get_channels()
        preset = channels.get(channel_id)
        if not preset:
            logger.warning("No preset entry found for channel %s", channel_id)
        elif preset.status != "active":
            logger.info("Channel %s preset status=%s", channel_id, preset.status)
        return preset


def infer_channel_id_from_path(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    match = CHANNEL_PATTERN.search(str(path))
    return match.group(0) if match else None

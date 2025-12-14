# Channel profile loader (SoT: packages/script_pipeline/channels/CHxx-*/channel_info.json)
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any

from factory_common.paths import script_pkg_root


# NOTE: this module lives at apps/ui-backend/backend/core/tools/channel_profile.py
PROJECT_ROOT = Path(__file__).resolve().parents[4]
CHANNELS_ROOT = script_pkg_root() / "channels"


@dataclass
class ChannelProfile:
    code: str
    name: Optional[str] = None
    description: Optional[str] = None
    audience_profile: Optional[str] = None
    persona_summary: Optional[str] = None
    script_prompt: Optional[str] = None


def _find_channel_dir(channel_code: str) -> Path:
    code = channel_code.upper()
    candidates = sorted(CHANNELS_ROOT.glob(f"{code}-*"))
    if not candidates:
        raise FileNotFoundError(f"channel directory not found for code={code}")
    return candidates[0]


def _load_channel_info(info_path: Path) -> Dict[str, Any]:
    try:
        return json.loads(info_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"channel_info.json not found: {info_path}") from exc
    except json.JSONDecodeError as exc:
        raise FileNotFoundError(f"channel_info.json is invalid: {info_path}") from exc


def load_channel_profile(channel_code: str) -> ChannelProfile:
    """
    Load channel profile from channel_info.json.
    - script_prompt is read from channel_info.json; falls back to script_prompt.txt if missing.
    - persona_summary falls back to audience_profile/persona.summary when present.
    """
    code = channel_code.upper()
    channel_dir = _find_channel_dir(code)
    info_path = channel_dir / "channel_info.json"
    payload = _load_channel_info(info_path)

    # prompt
    prompt = (payload.get("script_prompt") or "").strip()
    if not prompt:
        prompt_path = channel_dir / "script_prompt.txt"
        if prompt_path.exists():
            prompt = prompt_path.read_text(encoding="utf-8").strip()

    # persona summary fallback
    persona_summary = payload.get("persona_summary") or payload.get("persona")
    if isinstance(persona_summary, dict):
        persona_summary = persona_summary.get("summary") or persona_summary.get("target_audience")
    audience_profile = payload.get("audience_profile")
    if not persona_summary:
        persona_summary = audience_profile

    return ChannelProfile(
        code=code,
        name=payload.get("name") or payload.get("channel_id"),
        description=payload.get("description"),
        audience_profile=audience_profile,
        persona_summary=persona_summary,
        script_prompt=prompt or None,
    )

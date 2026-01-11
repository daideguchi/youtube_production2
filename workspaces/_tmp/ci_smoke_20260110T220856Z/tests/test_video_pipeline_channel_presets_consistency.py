from __future__ import annotations

import json
from pathlib import Path

from factory_common.paths import script_pkg_root, video_pkg_root


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_capcut_channels_have_channel_presets_entry() -> None:
    presets_path = video_pkg_root() / "config" / "channel_presets.json"
    presets = (_load_json(presets_path).get("channels") or {}) if presets_path.exists() else {}

    channels_root = script_pkg_root() / "channels"
    capcut_channels: list[str] = []
    for channel_dir in sorted(channels_root.glob("CH??-*")):
        info_path = channel_dir / "channel_info.json"
        if not info_path.exists():
            continue
        info = _load_json(info_path)
        channel_code = channel_dir.name.split("-", 1)[0].upper()
        if info.get("video_workflow") == "capcut":
            capcut_channels.append(channel_code)
            assert channel_code in presets, f"missing channel_presets.json entry for capcut channel: {channel_code}"

    assert capcut_channels, "expected at least one capcut channel in script_pipeline/channels"


def test_active_channel_presets_have_capcut_template() -> None:
    presets_path = video_pkg_root() / "config" / "channel_presets.json"
    presets = (_load_json(presets_path).get("channels") or {}) if presets_path.exists() else {}

    for channel_code, cfg in presets.items():
        if not isinstance(cfg, dict):
            continue
        status = (cfg.get("status") or "active").strip().lower()
        if status != "active":
            continue
        template = (cfg.get("capcut_template") or "").strip()
        assert template, f"active preset missing capcut_template: {channel_code}"


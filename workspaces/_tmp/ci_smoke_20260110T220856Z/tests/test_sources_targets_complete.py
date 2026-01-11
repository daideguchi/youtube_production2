import yaml
from pathlib import Path


def test_all_channels_have_target_chars_min_max():
    cfg_path = Path("configs/sources.yaml")
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    channels = data.get("channels") or {}
    assert isinstance(channels, dict) and channels, "configs/sources.yaml must define channels"

    missing = []
    bad_ranges = []
    for channel_code, cfg in sorted(channels.items()):
        if not isinstance(cfg, dict):
            missing.append(channel_code)
            continue
        if "target_chars_min" not in cfg or "target_chars_max" not in cfg:
            missing.append(channel_code)
            continue
        mn = cfg.get("target_chars_min")
        mx = cfg.get("target_chars_max")
        if not isinstance(mn, int) or not isinstance(mx, int) or mn <= 0 or mx <= 0 or mn > mx:
            bad_ranges.append((channel_code, mn, mx))

    assert not missing, f"Missing target_chars_min/max for: {missing}"
    assert not bad_ranges, f"Invalid target_chars ranges: {bad_ranges}"


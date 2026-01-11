import re
from pathlib import Path

import yaml


_RANGE_DIGITS = re.compile(r"(?P<min>\d{1,3}(?:,\d{3})?)\s*[〜~]\s*(?P<max>\d{1,3}(?:,\d{3})?)\s*字")
_RANGE_THOUSANDS = re.compile(r"(?P<min>\d+)\s*[〜~]\s*(?P<max>\d+)\s*千字")

_KEYWORDS = (
    "目標文字数",
    "文字数（必須）",
    "ゴール",
    "スクリプト全体",
    "出力仕様",
    "文字数は",
)


def _to_int(text: str) -> int:
    return int(text.replace(",", ""))


def _extract_declared_range(prompt_text: str) -> tuple[int, int] | None:
    """
    Best-effort: extract an explicit total-length range (min/max) from a channel prompt.
    - Only enforces when the prompt explicitly declares a min/max range (digits or 千字).
    - Skips per-section budgets ("文字数目安").
    """

    lines = prompt_text.splitlines()
    ctx = 0
    for line in lines:
        if any(k in line for k in _KEYWORDS):
            ctx = 4  # also scan a few following lines
        if ctx <= 0:
            continue
        ctx -= 1

        if "文字数目安" in line:
            continue

        m = _RANGE_DIGITS.search(line)
        if m:
            return (_to_int(m.group("min")), _to_int(m.group("max")))
        m = _RANGE_THOUSANDS.search(line)
        if m:
            return (int(m.group("min")) * 1000, int(m.group("max")) * 1000)

    return None


def test_channel_prompts_match_target_chars_when_declared():
    cfg = yaml.safe_load(Path("configs/sources.yaml").read_text(encoding="utf-8"))
    channels = cfg.get("channels") or {}
    assert isinstance(channels, dict) and channels, "configs/sources.yaml must define channels"

    mismatches: list[str] = []
    for channel_code, channel_cfg in sorted(channels.items()):
        if not isinstance(channel_cfg, dict):
            continue
        prompt_path = Path(str(channel_cfg.get("channel_prompt") or ""))
        if not prompt_path.exists():
            continue
        declared = _extract_declared_range(prompt_path.read_text(encoding="utf-8"))
        if not declared:
            continue

        expected = (channel_cfg.get("target_chars_min"), channel_cfg.get("target_chars_max"))
        if declared != expected:
            mismatches.append(
                f"{channel_code}: expected {expected[0]}-{expected[1]} but prompt declares {declared[0]}-{declared[1]} ({prompt_path})"
            )

    assert not mismatches, "Prompt/target_chars mismatch:\n" + "\n".join(mismatches)


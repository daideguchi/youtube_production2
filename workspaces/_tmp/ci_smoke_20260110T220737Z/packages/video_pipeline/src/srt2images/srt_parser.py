from __future__ import annotations
import re
from pathlib import Path
from typing import List, Dict

TIMECODE_RE = re.compile(
    r"^(?P<sh>\d{2}):(?P<sm>\d{2}):(?P<ss>\d{2})(?P<sms>[\.,]\d{1,3})?\s+-->\s+"
    r"(?P<eh>\d{2}):(?P<em>\d{2}):(?P<es>\d{2})(?P<ems>[\.,]\d{1,3})?\s*$"
)


def _tc_to_seconds(h: int, m: int, s: int, ms_part: str | None) -> float:
    ms = 0
    if ms_part:
        # ms_part includes sep ( "," or "." )
        frac = ms_part[1:]
        # normalize to milliseconds
        frac = (frac + "000")[:3]
        ms = int(frac)
    return h * 3600 + m * 60 + s + ms / 1000.0


def parse_srt(path: Path) -> List[Dict]:
    """Parse a simple SRT file into segments with start/end/text.

    Handles both comma and dot decimal separators for milliseconds.
    Returns list of dicts: {start, end, text}
    """
    text = path.read_text(encoding="utf-8", errors="ignore")
    # Normalize line endings
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    segments: List[Dict] = []
    i = 0
    n = len(lines)
    while i < n:
        # Skip blank and numeric index
        # Find timecode line
        while i < n and not TIMECODE_RE.match(lines[i]):
            i += 1
        if i >= n:
            break
        m = TIMECODE_RE.match(lines[i])
        i += 1
        if not m:
            continue
        sh, sm, ss, sms = int(m.group("sh")), int(m.group("sm")), int(m.group("ss")), m.group("sms")
        eh, em, es, ems = int(m.group("eh")), int(m.group("em")), int(m.group("es")), m.group("ems")
        start = _tc_to_seconds(sh, sm, ss, sms)
        end = _tc_to_seconds(eh, em, es, ems)

        # Collect text lines until blank
        txt_lines = []
        while i < n and lines[i].strip() != "":
            txt_lines.append(lines[i])
            i += 1
        # skip blank
        while i < n and lines[i].strip() == "":
            i += 1

        text_block = " ".join([t.strip() for t in txt_lines]).strip()
        if text_block:
            segments.append({"start": start, "end": end, "text": text_block})

    # Ensure sorted
    segments.sort(key=lambda x: x["start"])
    return segments


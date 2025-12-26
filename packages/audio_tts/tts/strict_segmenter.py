import re
from typing import List
from .strict_structure import AudioSegment

# Constants for Pauses
PAUSE_H1 = 1.0
PAUSE_H2 = 0.8
PAUSE_H3 = 0.8
PAUSE_PARAGRAPH = 0.0  # SSOT: blank lines must not act as pause markers
PAUSE_MARKER = 0.5  # Explicit pause marker (e.g. '---')
PAUSE_SENTENCE = 0.1  # SSOT: default connector pause
PAUSE_COMMA = 0.0 # Voicevox handles commas naturally, so 0 explicit pause usually.

# Segmentation Regex
# Split by 。, !, ? but keep the delimiter.
# Also handle "。」" or "！" etc.
RE_SPLIT = re.compile(r'([^。！？\n]+[。！？])')
RE_PAUSE_MARKER = re.compile(r"^-{3,}$")


def _apply_min_post_pause(segments: List[AudioSegment], pause_sec: float) -> None:
    if not segments:
        return
    if segments[-1].post_pause_sec < pause_sec:
        segments[-1].post_pause_sec = pause_sec


def _is_pause_marker(line: str) -> bool:
    """
    Treat explicit pause markers as non-spoken separators.
    SSOT: `---` is the only allowed pause marker in A-text.
    Examples:
      - --- / - - -
    """
    compact = re.sub(r"\s+", "", line)
    if not compact:
        return False
    return bool(RE_PAUSE_MARKER.fullmatch(compact))

def strict_segmentation(text: str) -> List[AudioSegment]:
    segments: List[AudioSegment] = []
    lines = text.splitlines()
    
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            # Blank line -> paragraph break (increase pause of previous segment)
            _apply_min_post_pause(segments, PAUSE_PARAGRAPH)
            continue

        # Explicit pause marker line (e.g. '---') -> do not speak it
        if _is_pause_marker(line):
            _apply_min_post_pause(segments, PAUSE_MARKER)
            continue
            
        # Heading Check
        if line.startswith("#"):
            # Add a paragraph-like pause before the heading when it follows normal text.
            _apply_min_post_pause(segments, PAUSE_PARAGRAPH)
            level = len(line) - len(line.lstrip("#"))
            content = line.lstrip("#").strip()
            
            pause = PAUSE_H1
            if level == 2: pause = PAUSE_H2
            elif level >= 3: pause = PAUSE_H3
            
            seg = AudioSegment(
                text=content,
                reading=None, # To be filled by Arbiter
                post_pause_sec=pause,
                is_heading=True,
                heading_level=level,
                original_line_index=i
            )
            segments.append(seg)
            continue
            
        # Normal Text Segmentation
        # First, check if it's a "Paragraph Start" (heuristic: if previous line was empty or heading)
        # For now, just split by sentence.
        
        # Split logic
        parts = RE_SPLIT.findall(line)
        # If no split found but text exists (no punctuation at end), take the whole line
        if not parts:
            parts = [line]
        else:
            # Check for leftovers
            joined = "".join(parts)
            if len(joined) < len(line):
                leftover = line[len(joined):]
                if leftover.strip():
                    parts.append(leftover)

        for j, part in enumerate(parts):
            part = part.strip()
            if not part:
                continue
                
            # Default pause is sentence pause
            pause = PAUSE_SENTENCE
            
            # If this is the last part of the line, use Paragraph Pause if structure implies
            # (But strictly, we rely on Markdown structure. For plain text lines, we treat them as sentences.)
            
            seg = AudioSegment(
                text=part,
                reading=None,
                post_pause_sec=pause,
                is_heading=False,
                original_line_index=i
            )
            segments.append(seg)

    return segments

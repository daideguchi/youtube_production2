#!/usr/bin/env python3
"""
Script to create image cues from SRT file for CapCut project
"""
import json
import re
from pathlib import Path
from typing import List, Dict, Any


def _truncate_summary(text: str, limit: int = 150) -> str:
    t = " ".join(text.split())
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


def parse_srt(path: Path) -> List[Dict]:
    """Parse a simple SRT file into segments with start/end/text."""
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


def make_cues(segments: List[Dict], target_imgdur: float = 30.0, fps: int = 30) -> List[Dict]:
    """
    Create image cues from SRT segments, grouping them into approximately target_imgdur chunks
    """
    cues: List[Dict] = []
    if not segments:
        return cues

    # Group segments into cues based on duration
    current_start = segments[0]["start"]
    current_text = ""
    current_segments = []
    
    for segment in segments:
        # Add current segment to the buffer
        if current_text:
            current_text += " " + segment["text"]
        else:
            current_text = segment["text"]
        current_segments.append(segment)
        
        # Calculate duration from the start of the first segment to the end of the current one
        current_duration = segment["end"] - current_start
        
        # If we've reached the target duration or this is the last segment, create a cue
        if current_duration >= target_imgdur or segment is segments[-1]:
            # Calculate the actual end time from the last segment in the group
            actual_end = current_segments[-1]["end"]
            duration = actual_end - current_start
            
            summary = _truncate_summary(current_text)
            
            cue = {
                "index": len(cues) + 1,
                "start_sec": round(current_start, 3),
                "end_sec": round(actual_end, 3),
                "duration_sec": round(duration, 3),
                "text": current_text,
                "summary": summary,
                "context_reason": "Approximate 30-second grouping",
                "emotional_tone": "narrative",
                "start_frame": int(round(current_start * fps)),
                "end_frame": int(round(actual_end * fps)),
                "duration_frames": max(1, int(round(actual_end * fps)) - int(round(current_start * fps))),
                "visual_focus": "Buddhist story scene",
                "section_type": "narrative",
                "use_persona": False,
                "role_tag": "story"
            }
            
            cues.append(cue)
            
            # Reset for the next cue
            if segment is not segments[-1]:
                current_start = segment["end"]
                current_text = ""
                current_segments = []

    return cues


def main():
    # Define paths
    srt_path = Path("/Users/dd/10_YouTube_Automation/factory_commentary/commentary_02_srt2images_timeline/input/CH01_人生の道標/220.srt")
    output_dir = Path("/Users/dd/10_YouTube_Automation/factory_commentary/commentary_02_srt2images_timeline/output/CH01_人生の道標_220")
    
    # Parse SRT
    print(f"Parsing SRT: {srt_path}")
    segments = parse_srt(srt_path)
    print(f"Parsed {len(segments)} segments")
    
    # Create cues
    print("Creating image cues...")
    cues = make_cues(segments, target_imgdur=30.0, fps=30)
    print(f"Created {len(cues)} cues")
    
    # Create image_cues.json
    image_cues_data = {
        "fps": 30,
        "size": {"width": 1920, "height": 1080},
        "crossfade": 0.5,
        "imgdur": 30.0,
        "cues": cues
    }
    
    image_cues_path = output_dir / "image_cues.json"
    with open(image_cues_path, 'w', encoding='utf-8') as f:
        json.dump(image_cues_data, f, indent=2, ensure_ascii=False)
    
    print(f"Image cues saved to: {image_cues_path}")
    
    # Create placeholder images
    images_dir = output_dir / "images"
    for i, cue in enumerate(cues, start=1):
        img_path = images_dir / f"{i:04d}.png"
        # Create a placeholder text file instead of an actual image
        with open(img_path.with_suffix('.txt'), 'w') as f:
            f.write(f"Placeholder for cue {i}\n")
            f.write(f"Duration: {cue['start_sec']:.2f}s to {cue['end_sec']:.2f}s\n")
            f.write(f"Summary: {cue['summary'][:100]}...\n")
    
    print(f"Created {len(cues)} placeholder files in {images_dir}")


if __name__ == "__main__":
    main()
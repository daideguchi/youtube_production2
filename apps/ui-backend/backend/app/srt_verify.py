from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List, Tuple

from backend.app.srt_models import SRTIssue, SRTVerifyResponse
from backend.audio import wav_tools

SRT_TIMESTAMP_PATTERN = re.compile(
    r"(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2}),(?P<millis>\d{3})"
)


def _parse_srt_timestamp(value: str) -> float:
    match = SRT_TIMESTAMP_PATTERN.match(value.strip())
    if not match:
        raise ValueError(f"Invalid timestamp: {value}")
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    second = int(match.group("second"))
    millis = int(match.group("millis"))
    return hour * 3600 + minute * 60 + second + millis / 1000.0


def _parse_block(lines: List[str]) -> Tuple[int, float, float]:
    index = int(lines[0].strip())
    start_raw, end_raw = lines[1].split("-->")
    start = _parse_srt_timestamp(start_raw)
    end = _parse_srt_timestamp(end_raw)
    if end < start:
        raise ValueError(f"SRT block {index}: end < start ({start} -> {end})")
    return index, start, end


def _iter_srt_blocks(path: Path) -> Iterable[Tuple[int, float, float]]:
    with path.open("r", encoding="utf-8") as handle:
        block: List[str] = []
        for line in handle:
            line = line.rstrip("\n")
            if line:
                block.append(line)
                continue
            if block:
                yield _parse_block(block)
                block = []
        if block:
            yield _parse_block(block)


def verify_srt_file(
    wav_path: Path,
    srt_path: Path,
    *,
    tolerance_ms: int,
) -> SRTVerifyResponse:
    issues: List[SRTIssue] = []
    valid = True
    try:
        audio_duration = wav_tools.duration_from_file(wav_path)
    except Exception as exc:  # pragma: no cover - propagate error info
        issues.append(SRTIssue(type="audio_error", detail=str(exc)))
        return SRTVerifyResponse(
            valid=False,
            audio_duration_seconds=None,
            srt_duration_seconds=None,
            diff_ms=None,
            issues=issues,
        )

    last_end = 0.0
    previous_end = 0.0
    block_count = 0
    try:
        for index, start, end in _iter_srt_blocks(srt_path):
            block_count += 1
            if start < previous_end:
                issues.append(
                    SRTIssue(
                        type="overlap",
                        detail=f"Block {index} overlaps previous end {previous_end:.3f}s",
                        block=index,
                        start=start,
                        end=end,
                    )
                )
                valid = False
            previous_end = end
            last_end = max(last_end, end)
    except ValueError as exc:
        issues.append(SRTIssue(type="parse_error", detail=str(exc)))
        return SRTVerifyResponse(
            valid=False,
            audio_duration_seconds=audio_duration,
            srt_duration_seconds=None,
            diff_ms=None,
            issues=issues,
        )

    srt_duration = last_end
    diff_ms = abs(audio_duration - srt_duration) * 1000.0
    if diff_ms > tolerance_ms:
        issues.append(
            SRTIssue(
                type="duration_mismatch",
                detail=f"diff={diff_ms:.1f}ms exceeds tolerance {tolerance_ms}ms",
            )
        )
        valid = False

    if block_count == 0:
        issues.append(SRTIssue(type="empty_srt", detail="SRT file contains no blocks"))
        valid = False

    return SRTVerifyResponse(
        valid=valid,
        audio_duration_seconds=audio_duration,
        srt_duration_seconds=srt_duration,
        diff_ms=diff_ms,
        issues=issues,
    )


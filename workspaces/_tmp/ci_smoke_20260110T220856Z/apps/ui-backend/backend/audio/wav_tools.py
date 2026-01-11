from __future__ import annotations

import wave
from pathlib import Path
from typing import Union


def duration_from_file(path: Union[str, Path]) -> float:
    wav_path = Path(path)
    with wave.open(str(wav_path), "rb") as handle:
        frames = handle.getnframes()
        rate = handle.getframerate()
        if not rate:
            raise ValueError(f"Invalid wav framerate: {wav_path}")
        return frames / float(rate)


def get_duration_seconds(path: str) -> float:
    # Back-compat alias
    return duration_from_file(path)

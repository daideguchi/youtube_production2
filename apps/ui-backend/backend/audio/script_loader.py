from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Union


@dataclass(frozen=True)
class ScriptSection:
    index: int
    lines: List[str]


def iterate_sections(path: Union[str, Path]) -> Iterable[ScriptSection]:
    """Iterate text sections separated by blank lines.

    Lines are returned without trailing newlines. Empty lines separate sections.
    """
    src_path = Path(path)
    text = src_path.read_text(encoding="utf-8")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")

    buf: List[str] = []
    idx = 1
    for raw in normalized.splitlines():
        line = raw.rstrip("\n")
        if not line.strip():
            if buf:
                yield ScriptSection(index=idx, lines=buf)
                idx += 1
                buf = []
            continue
        buf.append(line)
    if buf:
        yield ScriptSection(index=idx, lines=buf)

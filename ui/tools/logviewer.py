#!/usr/bin/env python3
"""Log viewer for UI backend/frontend logs."""

from __future__ import annotations

import argparse
import collections
import os
import time
from pathlib import Path
from typing import Iterable, Optional

YTM_ROOT = Path(__file__).resolve().parents[2]
LOG_ROOT = YTM_ROOT / "logs" / "ui_hub"
COMPONENT_LOGS = {
    "backend": LOG_ROOT / "backend.log",
    "frontend": LOG_ROOT / "frontend.log",
}


def tail_file(path: Path, lines: int) -> None:
    if not path.exists():
        print(f"{path}: log file not found")
        return
    dq: "collections.deque[str]" = collections.deque(maxlen=lines)
    with path.open("r") as fh:
        for line in fh:
            dq.append(line.rstrip("\n"))
    for line in dq:
        print(line)


def follow_file(path: Path) -> None:
    if not path.exists():
        print(f"{path}: log file not found")
        return
    with path.open("r") as fh:
        fh.seek(0, os.SEEK_END)
        while True:
            line = fh.readline()
            if not line:
                time.sleep(0.5)
                continue
            print(line.rstrip("\n"))


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="UI log viewer")
    parser.add_argument(
        "--component",
        choices=list(COMPONENT_LOGS) + ["all"],
        default="backend",
        help="Select backend/frontend/all (default: backend)",
    )
    parser.add_argument("-n", "--lines", type=int, default=200, help="Tail lines (default: 200)")
    parser.add_argument("-f", "--follow", action="store_true", help="Follow log output")
    args = parser.parse_args(list(argv) if argv is not None else None)

    targets = (
        COMPONENT_LOGS.items()
        if args.component == "all"
        else [(args.component, COMPONENT_LOGS[args.component])]
    )

    for name, path in targets:
        print(f"== {name} ({path}) ==")
        tail_file(path, args.lines)
        if args.follow:
            print(f"-- following {name} --")
            follow_file(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

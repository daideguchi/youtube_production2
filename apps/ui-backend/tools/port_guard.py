#!/usr/bin/env python3
"""Port monitoring utility for the unified UI stack."""

from __future__ import annotations

import argparse
import subprocess
from typing import Iterable, Optional

DEFAULT_PORTS = (8000, 3000)


def info_line(port: int, output: str) -> str:
    if not output.strip():
        return f"{port:<5} FREE"
    entries = ", ".join(output.strip().splitlines())
    return f"{port:<5} {entries}"


def query_port(port: int) -> str:
    cmd = ["lsof", "-i", f"tcp:{port}", "-P", "-n"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        # remove header line if present
        lines = result.stdout.strip().splitlines()
        if len(lines) > 1 and lines[0].startswith("COMMAND"):
            lines = lines[1:]
        return "\n".join(lines)
    except FileNotFoundError:
        raise SystemExit("lsof command not found. Install lsof to use port_guard.")


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="UI port guard")
    parser.add_argument(
        "ports",
        nargs="*",
        type=int,
        default=DEFAULT_PORTS,
        help="Ports to inspect (default: 8000 3000)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    for port in args.ports:
        output = query_port(port)
        print(info_line(port, output or ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

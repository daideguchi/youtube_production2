#!/usr/bin/env python3
"""Verify that the OpenRouter API key is valid before running workflows."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMMENTARY_ROOT = PROJECT_ROOT / "commentary_01_srtfile_v2"
if str(COMMENTARY_ROOT) not in sys.path:
    sys.path.insert(0, str(COMMENTARY_ROOT))

from qwen.openrouter_client import OpenRouterError, OpenRouterQwenClient  # type: ignore


def main() -> int:
    try:
        OpenRouterQwenClient()
    except OpenRouterError as exc:
        print(f"OpenRouter API key verification failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - defensive
        print(f"Unexpected error while verifying OpenRouter API key: {exc}", file=sys.stderr)
        return 1
    print("OpenRouter API key verified successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

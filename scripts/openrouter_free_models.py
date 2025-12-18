#!/usr/bin/env python3
"""List available OpenRouter free-tier models using factory_commentary utilities."""

from __future__ import annotations

import json
import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from _bootstrap import bootstrap

REPO_ROOT = bootstrap()

from script_pipeline.tools.openrouter_models import get_free_model_candidates  # noqa: E402
from scripts.env_guard import ensure_openrouter_key  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="List OpenRouter free-tier model candidates")
    parser.add_argument("--refresh", action="store_true", help="Force refresh model list")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ensure_openrouter_key()
    api_key = os.getenv("OPENROUTER_API_KEY")

    candidates = get_free_model_candidates(refresh=args.refresh)
    snapshot = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "selected_models": candidates,
    }
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

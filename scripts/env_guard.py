"""Common environment guards to prevent running without required keys."""

from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv(env_path: Path) -> None:
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def ensure_openrouter_key() -> None:
    """Fail fast if OPENROUTER_API_KEY is missing.

    1. Check current environment.
    2. If missing, attempt to load `<repo>/.env`.
    3. If still missing, abort with instructions.
    """

    if os.getenv("OPENROUTER_API_KEY"):
        return

    root = Path(__file__).resolve().parents[1]
    env_path = root / ".env"
    _load_dotenv(env_path)

    if os.getenv("OPENROUTER_API_KEY"):
        return

    raise SystemExit(
        "OPENROUTER_API_KEY が見つかりません。.env を設定し `source .env` または"
        " `python scripts/check_env.py --keys OPENROUTER_API_KEY` を通過させてから実行してください。"
    )


__all__ = ["ensure_openrouter_key"]

#!/usr/bin/env python3
"""Environment variable checker for factory_commentary."""
import argparse
import os
import sys
from pathlib import Path

REQUIRED_KEYS = [
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "PERPLEXITY_API_KEY",
    "GEMINI_API_KEY",
    "SERPAPI_API_KEY",
    "YOUTUBE_API_KEY",
    "VOICEVOX_SERVER_URL",
    "VOICEVOX_API_KEY",
    "PIXABAY_API_KEY",
    "UI_SESSION_TOKEN",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
]

WARNING_KEYS = {
    "GEMINI_API_KEY": "Gemini API key is managed via test.dd.1107.11107@gmail.com.",
    "UI_SESSION_TOKEN": "UI session token should match ssot/OPS_ENV_VARS.md guidance.",
}


def parse_env_file(path: Path) -> dict:
    env = {}
    if not path.exists():
        raise FileNotFoundError(f".env file not found: {path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate required environment variables for start_all")
    parser.add_argument("--env-file", default=str(Path.cwd() / ".env"), help="Path to .env file (default: ./ .env)")
    parser.add_argument(
        "--keys",
        nargs="+",
        help="Override the required key list (default: all REQUIRED_KEYS)",
    )
    args = parser.parse_args()

    env_path = Path(args.env_file)
    try:
        env = parse_env_file(env_path)
    except FileNotFoundError as exc:
        print(f"❌ {exc}")
        print("    Hint: copy factory_commentary/.env.example to .env and fill in values.")
        return 1

    required_keys = args.keys or REQUIRED_KEYS
    missing = [key for key in required_keys if not env.get(key)]
    if missing:
        print("❌ Missing required environment variables:")
        for key in missing:
            print(f"   - {key}")
        print("Please update .env and re-run this command.")
        return 1

    for key, message in WARNING_KEYS.items():
        if key in env:
            print(message)

    print("✅ All required environment variables are set")
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
]

WARNING_KEYS = {
    "GEMINI_API_KEY": "Gemini API key is managed via test.dd.1107.11107@gmail.com.",
    "UI_SESSION_TOKEN": "UI session token should match ssot/ops/OPS_ENV_VARS.md guidance.",
}

# Optional provider keys:
# - Azure is intentionally OPTIONAL for `./start.sh` so operators can disable it
#   (e.g., OpenRouter-only workflows) without being blocked at startup.
OPTIONAL_PAIRS = [
    ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"),
]

def _find_repo_root(start: Path) -> Path:
    cur = start if start.is_dir() else start.parent
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate.resolve()
    return cur.resolve()


DEFAULT_ENV_FILE = str(_find_repo_root(Path(__file__).resolve()) / ".env")


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
    parser.add_argument(
        "--env-file",
        default=DEFAULT_ENV_FILE,
        help="Path to .env file (default: <repo_root>/.env)",
    )
    parser.add_argument(
        "--keys",
        nargs="+",
        help="Override the required key list (default: all REQUIRED_KEYS)",
    )
    args = parser.parse_args()

    # Warn about global PYTHONPATH pollution (legacy repos can shadow imports).
    pythonpath = os.environ.get("PYTHONPATH") or ""
    if pythonpath:
        legacy_markers = ["youtube_master", "commentary_01_srtfile_v2"]
        if any(m in pythonpath for m in legacy_markers):
            print("⚠️  Detected global PYTHONPATH containing legacy paths. Consider `unset PYTHONPATH`.")
            print("    Safer: run via `./scripts/with_ytm_env.sh ...` which prepends the correct repo paths.")

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

    # Optional-but-consistent pairs: warn on partial config.
    for a, b in OPTIONAL_PAIRS:
        has_a = bool(env.get(a))
        has_b = bool(env.get(b))
        if has_a ^ has_b:
            missing_key = b if has_a else a
            present_key = a if has_a else b
            print(
                f"⚠️  Optional provider config incomplete: {missing_key} is missing "
                f"but {present_key} is set. Azure is optional; set both to enable Azure."
            )

    print("✅ All required environment variables are set")
    return 0


if __name__ == "__main__":
    sys.exit(main())

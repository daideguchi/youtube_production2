#!/usr/bin/env python3
"""Environment variable checker for factory_commentary."""
import argparse
import os
import sys
from pathlib import Path

from _bootstrap import bootstrap

REPO_ROOT = bootstrap(load_env=False)

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
DEFAULT_ENV_FILE = str(REPO_ROOT / ".env")


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


def _warn_workspace_layout(repo_root: Path) -> None:
    ws_root = repo_root / "workspaces"
    scripts_root = ws_root / "scripts"

    if not scripts_root.exists():
        print("⚠️  workspaces/scripts が見つかりません（台本SoTが消えている/退避された可能性）")
        print(f"    expected: {scripts_root}")
        return
    if scripts_root.is_symlink():
        print("⚠️  workspaces/scripts が symlink です（SoTは実ディレクトリ推奨）")
        print(f"    path: {scripts_root}")

    offloaded_sentinel = scripts_root / "README_OFFLOADED.txt"
    if offloaded_sentinel.exists():
        print("⚠️  workspaces/scripts が offload 済みの可能性があります（README_OFFLOADED.txt 検出）")
        print(f"    sentinel: {offloaded_sentinel}")

    planning_channels_dir = ws_root / "planning" / "channels"
    if not planning_channels_dir.exists() or not planning_channels_dir.is_dir():
        return

    planning_channels = {p.stem.upper() for p in planning_channels_dir.glob("CH*.csv") if p.is_file()}
    if not planning_channels:
        return

    script_channels = {p.name.upper() for p in scripts_root.glob("CH*") if p.is_dir()}
    missing = sorted(planning_channels - script_channels)
    if not missing:
        return

    shown = ", ".join(missing[:8])
    suffix = " ..." if len(missing) > 8 else ""
    print(f"ℹ️  scripts SoT 未作成チャンネルがあります: {len(missing)}件（未着手なら正常）")
    print(f"    missing (first): {shown}{suffix}")
    print("    NOTE: `./start.sh` は planning channels に合わせて空dirを自動作成します（必要なら再起動）。")
    print("    NOTE: UIのチャンネル一覧は Planning/ChannelProfile からも表示できます。台本画面のSoTは workspaces/scripts/CHxx/ です。")


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
        legacy_markers = ["youtube_master"]
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

    # Soft warnings: SoT directories (non-fatal)
    try:
        _warn_workspace_layout(REPO_ROOT)
    except Exception:
        pass

    print("✅ All required environment variables are set")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
Channel prompt schema sync utility.

Reads a channel prompt YAML (packages/script_pipeline/prompts/channels/*.yaml) and writes:
- script_prompt.txt under the specified channel directory
- channel_info.json (script_prompt / template_path / persona_path)

Usage:
PYTHONPATH=".:packages" python -m script_pipeline.tools.channel_prompt_sync --yaml packages/script_pipeline/prompts/channels/CH03.yaml --channel-dir "packages/script_pipeline/channels/CH03-【シニアの健康】朗読図書館"
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

import yaml


from factory_common.paths import repo_root

PROJECT_ROOT = repo_root()


def load_channel_prompt(yaml_path: Path) -> Dict[str, Any]:
    payload = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    cp = payload.get("channel_prompt")
    if not isinstance(cp, dict):
        raise SystemExit(f"channel_prompt not found in {yaml_path}")
    body = cp.get("prompt_body")
    if not isinstance(body, str) or not body.strip():
        raise SystemExit(f"prompt_body is missing/empty in {yaml_path}")
    cp["prompt_body"] = body.rstrip() + "\n"
    return cp


def sync_channel_prompt(yaml_path: Path, channel_dir: Path) -> None:
    cp = load_channel_prompt(yaml_path)
    channel_dir = channel_dir.resolve()
    prompt_path = channel_dir / "script_prompt.txt"
    info_path = channel_dir / "channel_info.json"

    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(cp["prompt_body"], encoding="utf-8")

    info: Dict[str, Any] = {}
    if info_path.exists():
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
        except Exception:
            info = {}

    # keep existing fields unless we need to override specific keys
    info["template_path"] = str(prompt_path.relative_to(PROJECT_ROOT))
    info["script_prompt"] = cp["prompt_body"]
    if cp.get("persona_path"):
        info["persona_path"] = cp["persona_path"]

    info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"synced prompt → {prompt_path}")
    print(f"synced channel_info → {info_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync channel prompt YAML to channel files.")
    parser.add_argument("--yaml", required=True, type=Path, help="Path to channel prompt YAML.")
    parser.add_argument("--channel-dir", required=True, type=Path, help="Path to channel dir (contains channel_info.json).")
    args = parser.parse_args()
    sync_channel_prompt(args.yaml, args.channel_dir)


if __name__ == "__main__":
    main()

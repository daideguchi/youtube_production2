"""
Channel prompt schema sync utility.

Reads a channel prompt YAML (packages/script_pipeline/prompts/channels/*.yaml) and writes:
- script_prompt.txt under the specified channel directory
- channel_info.json (script_prompt / template_path / persona_path)

Safety:
- By default, this tool refuses to overwrite an existing `script_prompt.txt` when the YAML `prompt_body`
  differs from the current prompt SSOT. This prevents accidental "prompt drift" in multi-agent operations.
- Use `--force` only when you intentionally want to overwrite the on-disk prompt from YAML.

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


def _normalize_text(text: str) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def sync_channel_prompt(yaml_path: Path, channel_dir: Path, *, force: bool) -> None:
    cp = load_channel_prompt(yaml_path)
    channel_dir = channel_dir.resolve()
    prompt_path = channel_dir / "script_prompt.txt"
    info_path = channel_dir / "channel_info.json"

    if prompt_path.exists() and not force:
        try:
            existing = _normalize_text(prompt_path.read_text(encoding="utf-8"))
        except Exception:
            existing = ""
        desired = _normalize_text(str(cp.get("prompt_body") or ""))
        if existing.rstrip() != desired.rstrip():
            raise SystemExit(
                "Refusing to overwrite existing script_prompt.txt because YAML prompt_body differs.\n"
                f"- yaml: {yaml_path}\n"
                f"- prompt: {prompt_path}\n\n"
                "Fix options:\n"
                "1) Treat script_prompt.txt as SSOT and sync YAML instead (recommended):\n"
                "   python3 scripts/ops/script_prompt_integrity_audit.py --channel "
                + str(cp.get("channel_id") or "").strip()
                + " --apply-yaml-sync\n"
                "2) If you intentionally want to overwrite the prompt from YAML, rerun with --force.\n"
            )

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
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing script_prompt.txt even when YAML prompt_body differs (DANGEROUS).",
    )
    args = parser.parse_args()
    sync_channel_prompt(args.yaml, args.channel_dir, force=bool(args.force))


if __name__ == "__main__":
    main()

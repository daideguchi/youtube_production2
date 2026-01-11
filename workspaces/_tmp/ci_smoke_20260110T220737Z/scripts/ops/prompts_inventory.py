#!/usr/bin/env python3
"""
prompts_inventory.py — プロンプト配置の棚卸し（prompts/PROMPTS_INDEX.md を生成）

目的:
  - 「プロンプトがどこにあるか」を1枚に集約し、迷いどころを消す
  - root `prompts/` への複製/同期を禁止し、正本を `packages/**` 側へ固定する

Usage:
  python3 scripts/ops/prompts_inventory.py --stdout
  python3 scripts/ops/prompts_inventory.py --write
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from _bootstrap import bootstrap


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _list_files(repo_root: Path, root: Path, *, exts: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    if not root.exists():
        return out
    for fp in root.rglob("*"):
        if not fp.is_file():
            continue
        if fp.suffix.lower() not in exts:
            continue
        rel = fp.relative_to(repo_root).as_posix()
        out.append(rel)
    return sorted(out)


def _render_section(title: str, paths: list[str]) -> list[str]:
    lines: list[str] = []
    lines.append(f"## {title}")
    lines.append("")
    if not paths:
        lines.append("- （なし）")
        lines.append("")
        return lines
    for p in paths:
        lines.append(f"- `{p}`")
    lines.append("")
    lines.append(f"- 件数: {len(paths)}")
    lines.append("")
    return lines


def build_index(repo_root: Path) -> str:
    script_prompts_root = repo_root / "packages" / "script_pipeline" / "prompts"
    script_channels_root = repo_root / "packages" / "script_pipeline" / "channels"
    video_pkg_root = repo_root / "packages" / "video_pipeline"
    root_prompts = repo_root / "prompts"

    script_common: list[str] = []
    if script_prompts_root.exists():
        for fp in sorted(script_prompts_root.glob("*.txt")):
            if fp.is_file():
                script_common.append(fp.relative_to(repo_root).as_posix())
    script_templates = _list_files(repo_root, script_prompts_root / "templates", exts=(".txt",))
    script_channel_yaml = _list_files(repo_root, script_prompts_root / "channels", exts=(".yaml", ".yml"))
    script_channel_prompts = _list_files(repo_root, script_channels_root, exts=(".txt",))
    script_channel_prompts = [p for p in script_channel_prompts if p.endswith("/script_prompt.txt")]

    video_system_prompt = []
    system_prompt_path = video_pkg_root / "system_prompt_for_image_generation.txt"
    if system_prompt_path.exists():
        video_system_prompt = [system_prompt_path.relative_to(repo_root).as_posix()]
    video_templates = _list_files(repo_root, video_pkg_root / "templates", exts=(".txt",))

    root_prompt_like = _list_files(repo_root, root_prompts, exts=(".txt", ".yaml", ".yml"))
    root_prompt_like = [
        p
        for p in root_prompt_like
        if p not in {"prompts/README.md", "prompts/PROMPTS_INDEX.md"}
        and not p.endswith("/README.md")
        and not p.endswith("/PROMPTS_INDEX.md")
    ]

    lines: list[str] = []
    lines.append("# PROMPTS_INDEX — プロンプト配置の正本一覧（自動生成）")
    lines.append("")
    lines.append(f"Generated: `{_now_iso_utc()}` by `scripts/ops/prompts_inventory.py`")
    lines.append("")
    lines.append("原則:")
    lines.append("- プロンプトの正本は `packages/**` 側（複製・同期しない）")
    lines.append("- root `prompts/` は索引/ハブ（このファイル含む）")
    lines.append("")
    lines.extend(_render_section("Script pipeline — 共通プロンプト（.txt）", script_common))
    lines.extend(_render_section("Script pipeline — テンプレ（templates/*.txt）", script_templates))
    lines.extend(_render_section("Script pipeline — チャンネル方針（prompts/channels/*.yaml）", script_channel_yaml))
    lines.extend(_render_section("Script pipeline — チャンネル固有（channels/**/script_prompt.txt）", script_channel_prompts))
    lines.extend(_render_section("Video — 画像生成 system prompt", video_system_prompt))
    lines.extend(_render_section("Video — 画像プロンプトテンプレ（templates/*.txt）", video_templates))

    if root_prompt_like:
        lines.append("## ⚠️ root prompts に prompt 本体が存在します（要整理）")
        lines.append("")
        for p in root_prompt_like:
            lines.append(f"- `{p}`")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate prompts/PROMPTS_INDEX.md (inventory).")
    ap.add_argument("--write", action="store_true", help="Write prompts/PROMPTS_INDEX.md")
    ap.add_argument("--stdout", action="store_true", help="Print to stdout (default)")
    args = ap.parse_args()

    repo_root = bootstrap(load_env=False)
    content = build_index(repo_root)
    if args.write:
        out_path = repo_root / "prompts" / "PROMPTS_INDEX.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")
        print(f"[prompts_inventory] wrote {out_path.relative_to(repo_root)}")
        return 0

    print(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

from factory_common.fact_check import run_fact_check_with_codex
from factory_common.paths import repo_root, video_root


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _load_yaml_sources(channel: str) -> Dict[str, Any]:
    import yaml

    repo = repo_root()
    global_path = repo / "configs" / "sources.yaml"
    local_path = repo / "packages" / "script_pipeline" / "config" / "sources.yaml"

    def _load(path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    doc = _load(global_path)
    local = _load(local_path)
    merged = {**doc, **local}
    channels = merged.get("channels") if isinstance(merged.get("channels"), dict) else {}
    return (channels or {}).get(str(channel).upper()) or {}


def _normalize_policy(value: str | None) -> str:
    raw = str(value or "").strip().lower().replace("-", "_")
    if raw in {"", "auto"}:
        return "auto"
    if raw in {"disabled", "disable", "off", "false", "0", "none", "no"}:
        return "disabled"
    if raw in {"required", "require", "enabled", "enable", "on", "true", "1", "yes"}:
        return "required"
    return "auto"


def _effective_fact_check_policy(channel: str, override: str | None) -> str:
    if override:
        return _normalize_policy(override)
    sources = _load_yaml_sources(channel)
    explicit = sources.get("fact_check_policy")
    if explicit is not None:
        return _normalize_policy(str(explicit))
    web = _normalize_policy(str(sources.get("web_search_policy") or "auto"))
    if web == "disabled":
        return "disabled"
    if web == "required":
        return "required"
    return "auto"


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate fact_check_report.json via codex exec (read-only)")
    ap.add_argument("--channel", required=True)
    ap.add_argument("--video", required=True)
    ap.add_argument("--policy", default=None, help="disabled|auto|required (override)")
    args = ap.parse_args()

    channel = str(args.channel).upper().strip()
    video = str(args.video).zfill(3)
    base = video_root(channel, video)
    content_dir = base / "content"
    a_text_path = content_dir / "assembled_human.md"
    if not a_text_path.exists():
        a_text_path = content_dir / "assembled.md"

    a_text = _read_text(a_text_path)
    research_dir = content_dir / "analysis" / "research"
    search_results_path = research_dir / "search_results.json"
    wikipedia_summary_path = research_dir / "wikipedia_summary.json"
    references_path = research_dir / "references.json"
    out_path = research_dir / "fact_check_report.json"

    policy = _effective_fact_check_policy(channel, args.policy)
    report = run_fact_check_with_codex(
        channel=channel,
        video=video,
        a_text=a_text,
        policy=policy,
        search_results_path=search_results_path,
        wikipedia_summary_path=wikipedia_summary_path,
        references_path=references_path,
        output_path=out_path,
    )
    verdict = str(report.get("verdict") or "")
    print(json.dumps({"output": str(out_path), "policy": policy, "verdict": verdict}, ensure_ascii=False))
    if policy == "required" and verdict != "pass":
        return 2
    if verdict == "fail":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


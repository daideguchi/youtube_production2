#!/usr/bin/env python3
"""
LLM provenance report for a single episode.

Goal:
  "Which provider/model generated this script output?" is always answerable
  without polluting A-text / narration text with metadata.

Reads (best-effort):
  - workspaces/scripts/{CH}/{NNN}/status.json : stages.*.details.llm_calls[]
  - workspaces/scripts/{CH}/{NNN}/artifacts/llm/*.json : ytm.llm_text_output.v1

Usage:
  python3 scripts/llm_provenance_report.py --channel CH06 --video 004
  python3 scripts/llm_provenance_report.py --channel CH06 --video 004 --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _bootstrap_sys_path() -> None:
    """
    Make repo-root and `packages/` importable even when running from `scripts/`.
    (Do not rely on sitecustomize.py, which is not auto-loaded in this mode.)
    """

    start = Path(__file__).resolve()
    cur = start.parent
    repo = None
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            repo = candidate
            break
    if repo is None:
        raise SystemExit("repo root not found (pyproject.toml missing)")

    for path in (repo, repo / "packages"):
        p = str(path)
        if p not in sys.path:
            sys.path.insert(0, p)


_bootstrap_sys_path()

from factory_common.paths import status_path  # noqa: E402


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_get(d: Any, *keys: str) -> Any:
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _load_status_calls(path: Path) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not path.exists():
        return out
    data = _read_json(path)
    stages = data.get("stages") if isinstance(data, dict) else None
    if not isinstance(stages, dict):
        return out
    for stage_name, stage_state in stages.items():
        calls = _safe_get(stage_state, "details", "llm_calls")
        if not isinstance(calls, list) or not calls:
            continue
        last = calls[-1] if isinstance(calls[-1], dict) else None
        if not last:
            continue
        out[str(stage_name)] = last
    return out


def _load_llm_artifacts(base_dir: Path) -> Dict[str, Dict[str, Any]]:
    """
    Returns {stage: {artifact_path, generated_at, llm_meta, task, status}} for latest artifact per stage.
    """

    out: Dict[str, Dict[str, Any]] = {}
    artifacts_dir = base_dir / "artifacts" / "llm"
    if not artifacts_dir.exists():
        return out
    for path in sorted(artifacts_dir.glob("*.json")):
        try:
            obj = _read_json(path)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        stage = obj.get("stage")
        if not stage:
            continue
        out[str(stage)] = {
            "source": "artifact",
            "artifact_path": str(path),
            "generated_at": obj.get("generated_at"),
            "status": obj.get("status"),
            "task": obj.get("task"),
            "llm_meta": obj.get("llm_meta") or {},
        }
    return out


def _summarize_call(stage: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    # Normalize keys across (status.json llm_calls) and (artifact.llm_meta)
    if payload.get("source") == "artifact":
        meta = payload.get("llm_meta") or {}
        return {
            "stage": stage,
            "source": "artifact",
            "task": payload.get("task"),
            "provider": meta.get("provider"),
            "model": meta.get("model"),
            "chain": meta.get("chain"),
            "request_id": meta.get("request_id"),
            "latency_ms": meta.get("latency_ms"),
            "usage": meta.get("usage"),
            "finish_reason": meta.get("finish_reason"),
            "routing": meta.get("routing"),
            "cache": meta.get("cache"),
            "generated_at": payload.get("generated_at"),
            "artifact_path": payload.get("artifact_path"),
        }
    return {
        "stage": stage,
        "source": payload.get("source") or "api",
        "task": payload.get("task"),
        "provider": payload.get("provider"),
        "model": payload.get("model"),
        "chain": payload.get("chain"),
        "request_id": payload.get("request_id"),
        "latency_ms": payload.get("latency_ms"),
        "usage": payload.get("usage"),
        "finish_reason": payload.get("finish_reason"),
        "routing": payload.get("routing"),
        "cache": payload.get("cache"),
        "generated_at": payload.get("generated_at"),
        "artifact_path": payload.get("artifact"),
        "prompt_log": payload.get("prompt_log"),
        "resp_log": payload.get("resp_log"),
    }


def _format_routing(r: Any) -> str:
    if not isinstance(r, dict):
        return ""
    policy = r.get("policy") or ""
    pref = r.get("preferred_provider") or ""
    ratio = r.get("ratio")
    bucket = r.get("bucket")
    parts = []
    if policy:
        parts.append(f"policy={policy}")
    if pref:
        parts.append(f"preferred={pref}")
    if isinstance(ratio, (int, float)):
        parts.append(f"ratio={ratio:g}")
    if isinstance(bucket, (int, float)):
        parts.append(f"bucket={bucket:.4f}")
    return " ".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser(description="Show which LLM/provider generated each stage output for an episode.")
    ap.add_argument("--channel", required=True, help="e.g. CH06")
    ap.add_argument("--video", required=True, help="e.g. 004 (or 4)")
    ap.add_argument("--json", action="store_true", help="Output as JSON")
    args = ap.parse_args()

    channel = str(args.channel).strip().upper()
    video = str(args.video).strip()
    if video.isdigit():
        video = f"{int(video):03d}"

    sp = status_path(channel, video)
    base = sp.parent

    status_calls = _load_status_calls(sp)
    artifacts = _load_llm_artifacts(base)

    stages = sorted(set(status_calls.keys()) | set(artifacts.keys()))
    report: Dict[str, Any] = {
        "channel": channel,
        "video": video,
        "status_path": str(sp),
        "base_dir": str(base),
        "stages": {},
    }

    for stage in stages:
        payload = status_calls.get(stage) or artifacts.get(stage)
        if not payload:
            continue
        report["stages"][stage] = _summarize_call(stage, payload)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    print(f"{channel}-{video}")
    print(f"status: {sp}")
    if not report["stages"]:
        print("(no provenance found)")
        return

    for stage in sorted(report["stages"].keys()):
        row = report["stages"][stage]
        provider = row.get("provider") or "?"
        model = row.get("model") or "?"
        task = row.get("task") or ""
        req = row.get("request_id") or ""
        routing = _format_routing(row.get("routing"))
        extra = f" {routing}" if routing else ""
        print(f"- {stage}: {provider} {model} task={task} request_id={req}{extra}")


if __name__ == "__main__":
    main()


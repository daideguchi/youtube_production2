#!/usr/bin/env python3
from __future__ import annotations

"""
llm_usage_report.py — Per-episode LLM cost/usage report (token-based)

Reads: workspaces/logs/llm_usage.jsonl (default)
Filters by: routing_key (recommended: CHxx-NNN)

Why:
- 1本あたりのLLMコスト感（呼び出し回数/トークン量）を、運用者が即座に把握する。
- 特に高コストモデル（例: Opus）を「1回だけ」差し込む設計の監視に使う。
"""

import argparse
import json
import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from factory_common.paths import logs_root


def _norm_channel(value: str) -> str:
    return str(value or "").strip().upper()


def _norm_video(value: str) -> str:
    raw = str(value or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        raise SystemExit(f"invalid --video: {value!r}")
    return f"{int(digits):03d}"


def _default_log_path() -> Path:
    env = str(os.getenv("LLM_ROUTER_LOG_PATH") or "").strip()
    if env:
        return Path(env)
    return logs_root() / "llm_usage.jsonl"


def _iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.exists():
        raise SystemExit(f"llm usage log not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict):
                yield obj


@dataclass(frozen=True)
class UsageAgg:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def add(self, usage: Dict[str, Any] | None) -> "UsageAgg":
        if not isinstance(usage, dict):
            return UsageAgg(
                calls=self.calls + 1,
                prompt_tokens=self.prompt_tokens,
                completion_tokens=self.completion_tokens,
                total_tokens=self.total_tokens,
            )
        pt = int(usage.get("prompt_tokens") or 0)
        ct = int(usage.get("completion_tokens") or 0)
        tt = int(usage.get("total_tokens") or (pt + ct))
        return UsageAgg(
            calls=self.calls + 1,
            prompt_tokens=self.prompt_tokens + pt,
            completion_tokens=self.completion_tokens + ct,
            total_tokens=self.total_tokens + tt,
        )


def _pick_routing_key(args: argparse.Namespace) -> str:
    rk = str(args.routing_key or "").strip()
    if rk:
        return rk
    if args.channel and args.video:
        return f"{_norm_channel(args.channel)}-{_norm_video(args.video)}"
    raise SystemExit("specify --routing-key or (--channel and --video)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Per-episode LLM usage report (token-based).")
    ap.add_argument("--log", default="", help="Path to llm_usage.jsonl (default: env LLM_ROUTER_LOG_PATH or workspaces/logs/llm_usage.jsonl)")
    ap.add_argument("--routing-key", default="", help="Exact routing_key to filter (recommended: CHxx-NNN)")
    ap.add_argument("--channel", default="", help="Alternative to --routing-key (e.g. CH10)")
    ap.add_argument("--video", default="", help="Alternative to --routing-key (e.g. 010)")
    ap.add_argument("--task-prefix", default="", help="Optional task prefix filter (e.g. script_)")
    ap.add_argument("--json", action="store_true", help="Output JSON (default: human-readable)")
    args = ap.parse_args()

    rk = _pick_routing_key(args)
    task_prefix = str(args.task_prefix or "").strip()
    log_path = Path(str(args.log).strip()) if str(args.log).strip() else _default_log_path()

    by_task: Dict[str, UsageAgg] = {}
    by_model: Dict[Tuple[str, str], UsageAgg] = {}
    by_task_cache: Dict[str, UsageAgg] = {}
    by_model_cache: Dict[Tuple[str, str], UsageAgg] = {}

    total_api = UsageAgg()
    total_cache = UsageAgg()
    matched_api = 0
    matched_cache = 0

    for row in _iter_jsonl(log_path):
        if str(row.get("status") or "").strip().lower() != "success":
            continue
        if str(row.get("routing_key") or "").strip() != rk:
            continue
        task = str(row.get("task") or "").strip() or "(unknown_task)"
        if task_prefix and not task.startswith(task_prefix):
            continue
        usage = row.get("usage") if isinstance(row, dict) else None
        model = str(row.get("model") or "").strip() or "(unknown_model)"
        provider = str(row.get("provider") or "").strip() or "(unknown_provider)"

        cache_obj = row.get("cache") if isinstance(row, dict) else None
        is_cache_hit = isinstance(cache_obj, dict) and bool(cache_obj.get("hit"))

        if is_cache_hit:
            total_cache = total_cache.add(usage)
            by_task_cache[task] = by_task_cache.get(task, UsageAgg()).add(usage)
            by_model_cache[(provider, model)] = by_model_cache.get((provider, model), UsageAgg()).add(usage)
            matched_cache += 1
            continue

        total_api = total_api.add(usage)
        by_task[task] = by_task.get(task, UsageAgg()).add(usage)
        by_model[(provider, model)] = by_model.get((provider, model), UsageAgg()).add(usage)
        matched_api += 1

    total_all = UsageAgg(
        calls=total_api.calls + total_cache.calls,
        prompt_tokens=total_api.prompt_tokens + total_cache.prompt_tokens,
        completion_tokens=total_api.completion_tokens + total_cache.completion_tokens,
        total_tokens=total_api.total_tokens + total_cache.total_tokens,
    )

    payload = {
        "routing_key": rk,
        "log_path": str(log_path),
        "filters": {"task_prefix": task_prefix or None},
        "matched_calls": {"api": matched_api, "cache_hit": matched_cache, "total": matched_api + matched_cache},
        "total": {
            "api": {
                "calls": total_api.calls,
                "prompt_tokens": total_api.prompt_tokens,
                "completion_tokens": total_api.completion_tokens,
                "total_tokens": total_api.total_tokens,
            },
            "cache_hit": {
                "calls": total_cache.calls,
                "prompt_tokens": total_cache.prompt_tokens,
                "completion_tokens": total_cache.completion_tokens,
                "total_tokens": total_cache.total_tokens,
            },
            "all": {
                "calls": total_all.calls,
                "prompt_tokens": total_all.prompt_tokens,
                "completion_tokens": total_all.completion_tokens,
                "total_tokens": total_all.total_tokens,
            },
        },
        "by_task": {
            k: {
                "calls": v.calls,
                "prompt_tokens": v.prompt_tokens,
                "completion_tokens": v.completion_tokens,
                "total_tokens": v.total_tokens,
            }
            for k, v in sorted(by_task.items(), key=lambda kv: (-kv[1].total_tokens, kv[0]))
        },
        "by_model": {
            f"{prov}:{model}": {
                "calls": v.calls,
                "prompt_tokens": v.prompt_tokens,
                "completion_tokens": v.completion_tokens,
                "total_tokens": v.total_tokens,
            }
            for (prov, model), v in sorted(by_model.items(), key=lambda kv: (-kv[1].total_tokens, kv[0]))
        },
        "by_task_cache_hit": {
            k: {
                "calls": v.calls,
                "prompt_tokens": v.prompt_tokens,
                "completion_tokens": v.completion_tokens,
                "total_tokens": v.total_tokens,
            }
            for k, v in sorted(by_task_cache.items(), key=lambda kv: (-kv[1].total_tokens, kv[0]))
        },
        "by_model_cache_hit": {
            f"{prov}:{model}": {
                "calls": v.calls,
                "prompt_tokens": v.prompt_tokens,
                "completion_tokens": v.completion_tokens,
                "total_tokens": v.total_tokens,
            }
            for (prov, model), v in sorted(by_model_cache.items(), key=lambda kv: (-kv[1].total_tokens, kv[0]))
        },
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"routing_key: {rk}")
    print(f"log: {log_path}")
    if task_prefix:
        print(f"task_prefix: {task_prefix}")
    print("")
    print(
        "calls:"
        f" api={payload['total']['api']['calls']}"
        f" cache_hit={payload['total']['cache_hit']['calls']}"
        f" total={payload['total']['all']['calls']}"
    )
    print(
        "tokens(api):"
        f" prompt={payload['total']['api']['prompt_tokens']}"
        f" completion={payload['total']['api']['completion_tokens']}"
        f" total={payload['total']['api']['total_tokens']}"
    )
    if payload["total"]["cache_hit"]["calls"]:
        print(
            "tokens(cache_hit):"
            f" prompt={payload['total']['cache_hit']['prompt_tokens']}"
            f" completion={payload['total']['cache_hit']['completion_tokens']}"
            f" total={payload['total']['cache_hit']['total_tokens']}"
        )
    print("")
    print("top tasks:")
    for i, (task, agg) in enumerate(sorted(by_task.items(), key=lambda kv: (-kv[1].total_tokens, kv[0]))[:12], 1):
        print(f"{i:02d}. {task}: calls={agg.calls} total_tokens={agg.total_tokens}")
    print("")
    print("top models:")
    for i, ((prov, model), agg) in enumerate(sorted(by_model.items(), key=lambda kv: (-kv[1].total_tokens, kv[0]))[:12], 1):
        print(f"{i:02d}. {prov}:{model}: calls={agg.calls} total_tokens={agg.total_tokens}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

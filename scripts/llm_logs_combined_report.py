from __future__ import annotations
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List

try:
    from factory_common.paths import logs_root

    _LOGS_ROOT = logs_root()
    DEFAULT_LOGS = [
        str(_LOGS_ROOT / "llm_usage.jsonl"),
        str(_LOGS_ROOT / "llm_context_analyzer.log"),
        str(_LOGS_ROOT / "tts_llm_usage.log"),
    ]
except Exception:
    DEFAULT_LOGS = [
        "workspaces/logs/llm_usage.jsonl",
        "workspaces/logs/llm_context_analyzer.log",
        "workspaces/logs/tts_llm_usage.log",
    ]


def load(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def summarize(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(records)
    models = Counter()
    statuses = Counter()
    providers = Counter()
    latencies: List[int] = []
    req_ids: List[str] = []
    tokens = defaultdict(int)
    for rec in records:
        model = rec.get("model") or "(none)"
        provider = rec.get("provider") or "(none)"
        status = rec.get("status") or rec.get("task") or "unknown"
        models[model] += 1
        providers[provider] += 1
        statuses[status] += 1
        lat = rec.get("latency_ms")
        if isinstance(lat, (int, float)):
            latencies.append(int(lat))
        rid = rec.get("request_id")
        if isinstance(rid, str):
            req_ids.append(rid)
        usage = rec.get("usage") or {}
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            v = usage.get(k)
            if isinstance(v, int):
                tokens[k] += v
    return {
        "total": total,
        "statuses": statuses.most_common(),
        "models": models.most_common(),
        "providers": providers.most_common(),
        "latency_avg_ms": int(sum(latencies) / len(latencies)) if latencies else 0,
        "latency_p50_ms": sorted(latencies)[len(latencies)//2] if latencies else 0,
        "latency_max_ms": max(latencies) if latencies else 0,
        "tokens": dict(tokens),
        "last_request_id": req_ids[-1] if req_ids else None,
    }


def main():
    ap = argparse.ArgumentParser(description="Combined LLM logs report")
    ap.add_argument("--logs", nargs="*", default=DEFAULT_LOGS, help="log files to summarize")
    args = ap.parse_args()

    report = {}
    for path_str in args.logs:
        p = Path(path_str)
        recs = load(p)
        report[p.name] = summarize(recs)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

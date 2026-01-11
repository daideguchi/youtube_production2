#!/usr/bin/env python3
"""
Fireworks key pool report (no secrets).

Purpose:
- Show which Fireworks keys are usable/suspended (412) without printing raw keys.
- Attribute LLMRouter usage to keys (via `fireworks_key_fp` in llm_usage.jsonl).
- Optional: map key fingerprints to account emails from the legacy memo.

Notes:
- This script NEVER prints full API keys.
- Memo parsing is best-effort; it only needs (email + fw_...) on the same line.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from _bootstrap import bootstrap


bootstrap(load_env=False)

from factory_common import fireworks_keys  # noqa: E402
from factory_common.paths import logs_root, workspace_root  # noqa: E402


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_FW_KEY_RE = re.compile(r"fw_[A-Za-z0-9_-]{10,}")


@dataclass(frozen=True)
class MemoKeyEntry:
    email: str
    key_fp: str
    masked_key: str


def _sha256_hex(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace") or "null")
    except Exception:
        return None


def _load_state_map(pool: str) -> Dict[str, Dict[str, Any]]:
    sp = fireworks_keys.state_path(pool)
    obj = _load_json(sp)
    if not isinstance(obj, dict):
        return {}
    keys_obj = obj.get("keys")
    return keys_obj if isinstance(keys_obj, dict) else {}


def _iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict):
                yield obj


def _parse_memo(path: Path) -> List[MemoKeyEntry]:
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []

    by_fp: Dict[str, MemoKeyEntry] = {}
    current_email = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        emails = _EMAIL_RE.findall(line)
        key_m = _FW_KEY_RE.search(line)

        # Common legacy format in this repo:
        #   someone@example.com
        #   - fw_xxx...
        #   - note...
        if emails and not key_m:
            current_email = emails[0]

        if key_m:
            key = str(key_m.group(0) or "").strip()
            email = emails[0] if emails else current_email
            if not email or not key:
                continue
            try:
                fp = _sha256_hex(key)
            except Exception:
                continue
            by_fp.setdefault(
                fp,
                MemoKeyEntry(
                    email=email,
                    key_fp=fp,
                    masked_key=fireworks_keys.mask_key(key),
                ),
            )

    return list(by_fp.values())


def _sum_usage_tokens(usage: Any) -> int:
    if not isinstance(usage, dict):
        return 0
    for k in ("total_tokens", "tokens"):
        v = usage.get(k)
        if isinstance(v, int):
            return max(0, v)
        if isinstance(v, str) and v.isdigit():
            return max(0, int(v))
    pt = usage.get("prompt_tokens")
    ct = usage.get("completion_tokens")
    try:
        return max(0, int(pt or 0) + int(ct or 0))
    except Exception:
        return 0


def _build_usage_by_key_fp(log_path: Path) -> Tuple[Dict[str, Dict[str, int]], int]:
    """
    Returns:
      - per_fp: { fp: {calls, tokens} }
      - unattributed_calls: fireworks provider calls without fireworks_key_fp
    """
    per: Dict[str, Dict[str, int]] = defaultdict(lambda: {"calls": 0, "tokens": 0})
    unattributed = 0
    for obj in _iter_jsonl(log_path):
        if str(obj.get("provider") or "") != "fireworks":
            continue
        if str(obj.get("status") or "") != "success":
            continue
        fp = str(obj.get("fireworks_key_fp") or "").strip()
        if not fp:
            unattributed += 1
            continue
        per[fp]["calls"] += 1
        per[fp]["tokens"] += _sum_usage_tokens(obj.get("usage"))
    return dict(per), unattributed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pool", choices=["script", "image"], default="script")
    parser.add_argument(
        "--memo",
        type=str,
        default="",
        help="legacy memo path (default: workspaces/_scratch/fireworks_apiメモ)",
    )
    parser.add_argument(
        "--usage-log",
        type=str,
        default="",
        help="llm usage jsonl path (default: workspaces/logs/llm_usage.jsonl)",
    )
    parser.add_argument("--format", choices=["tsv", "json"], default="tsv")
    args = parser.parse_args()

    pool = str(args.pool or "script")
    memo_path = (
        Path(args.memo).expanduser().resolve()
        if str(args.memo or "").strip()
        else (workspace_root() / "_scratch" / "fireworks_apiメモ")
    )
    usage_log = Path(args.usage_log).expanduser().resolve() if str(args.usage_log or "").strip() else (logs_root() / "llm_usage.jsonl")

    memo_entries = _parse_memo(memo_path)
    memo_by_fp = {e.key_fp: e for e in memo_entries}

    state = _load_state_map(pool)
    usage_by_fp, unattributed_calls = _build_usage_by_key_fp(usage_log)

    rows: List[Dict[str, Any]] = []
    fps = set(state.keys()) | set(memo_by_fp.keys()) | set(usage_by_fp.keys())
    for fp in sorted(fps):
        ent = state.get(fp) if isinstance(state.get(fp), dict) else {}
        status = str((ent or {}).get("status") or "unknown")
        last_http = (ent or {}).get("last_http_status")
        email = (memo_by_fp.get(fp).email if fp in memo_by_fp else "")
        masked = (memo_by_fp.get(fp).masked_key if fp in memo_by_fp else "")
        u = usage_by_fp.get(fp, {"calls": 0, "tokens": 0})
        rows.append(
            {
                "key_fp": fp,
                "email": email,
                "masked_key": masked,
                "status": status,
                "last_http_status": last_http,
                "calls": int(u.get("calls") or 0),
                "tokens": int(u.get("tokens") or 0),
            }
        )

    if args.format == "json":
        out = {
            "pool": pool,
            "memo_path": str(memo_path),
            "usage_log": str(usage_log),
            "unattributed_calls": unattributed_calls,
            "rows": rows,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    # tsv
    header = ["status", "last_http_status", "calls", "tokens", "email", "masked_key", "key_fp"]
    print("\t".join(header))
    for r in sorted(rows, key=lambda x: (str(x.get("status") or ""), -int(x.get("calls") or 0), str(x.get("email") or ""))):
        print(
            "\t".join(
                [
                    str(r.get("status") or ""),
                    "" if r.get("last_http_status") is None else str(r.get("last_http_status")),
                    str(int(r.get("calls") or 0)),
                    str(int(r.get("tokens") or 0)),
                    str(r.get("email") or ""),
                    str(r.get("masked_key") or ""),
                    str(r.get("key_fp") or ""),
                ]
            )
        )
    if unattributed_calls:
        print(f"# note\tunattributed_fireworks_calls\t{unattributed_calls}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

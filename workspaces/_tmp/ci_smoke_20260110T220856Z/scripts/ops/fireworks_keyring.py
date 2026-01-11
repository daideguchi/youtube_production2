#!/usr/bin/env python3
from __future__ import annotations

"""
fireworks_keyring.py — Fireworks APIキーのキーローテ用ストアを管理する（台本/画像）

目的:
- repo 内（workspaces/_scratch など）に秘密鍵を置かずに、運用者が「キーを追加するだけ」で
  ローテーションできるようにする。

既定:
- pool=script（台本）: ~/.ytm/secrets/fireworks_script_keys.txt
- pool=image（画像） : ~/.ytm/secrets/fireworks_image_keys.txt
  - ルート変更: YTM_SECRETS_ROOT または --path

ファイル形式:
- 1行1キー（推奨）
- コメント: 先頭 '#'
- ENV 風: FIREWORKS_SCRIPT=...（右辺だけ抽出）

安全:
- このツールはキーをフルで表示しない（--show-masked でもマスク表示）。
"""

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from factory_common.paths import secrets_root, workspace_root


def _dedupe_keep_order(items: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for it in items:
        s = str(it or "").strip()
        if not s or s in seen:
            continue
        out.append(s)
        seen.add(s)
    return out


def _parse_keys(text: str) -> List[str]:
    key_re = re.compile(r"^fw_[A-Za-z0-9_-]{10,}$")
    keys: List[str] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            _left, right = line.split("=", 1)
            line = right.strip()
        # Allow inline comments: fw_xxx... # note
        if "#" in line:
            line = line.split("#", 1)[0].strip()
        line = line.strip().strip("'\"")
        # Stored as ASCII tokens (no spaces).
        if " " in line or "\t" in line:
            continue
        if not all(ord(ch) < 128 for ch in line):
            continue
        if not key_re.match(line):
            continue
        keys.append(line)
    return _dedupe_keep_order(keys)


def _extract_fw_keys_anywhere(text: str) -> List[str]:
    """
    Best-effort extractor for legacy memos (keys may be embedded in messy text).
    """
    tokens = re.findall(r"fw_[A-Za-z0-9_-]{10,}", str(text or ""))
    return _dedupe_keep_order(tokens)


def _read_keys(path: Path) -> List[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    return _parse_keys(text)


def _write_keys(path: Path, keys: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(keys) + ("\n" if keys else "")
    path.write_text(content, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def _default_keyring_path() -> Path:
    return secrets_root() / "fireworks_script_keys.txt"


def _default_keyring_path_for_pool(pool: str) -> Path:
    p = str(pool or "").strip().lower()
    if p == "script":
        return secrets_root() / "fireworks_script_keys.txt"
    if p == "image":
        return secrets_root() / "fireworks_image_keys.txt"
    raise SystemExit(f"invalid --pool: {pool!r} (expected: script|image)")


def _legacy_memo_path() -> Path:
    return workspace_root() / "_scratch" / "fireworks_apiメモ"


def _mask(key: str) -> str:
    k = str(key or "")
    if len(k) <= 8:
        return "*" * len(k)
    return f"{k[:4]}…{k[-4:]}"


def _matches_key(key: str, pattern: str) -> bool:
    """
    Match helper for operator workflows without exposing full keys.

    Supported patterns:
    - Exact key (fw_...) (not recommended to paste, but supported)
    - Mask-like patterns with ellipsis, e.g.:
        fw_1234…abcd
        fw_1234...abcd
      -> prefix/suffix match against the full key
    - Exact match against this tool's mask output (first4…last4)
    """
    k = str(key or "").strip()
    p = str(pattern or "").strip().strip("'\"")
    if not k or not p:
        return False
    if p == k:
        return True
    # Accept both unicode ellipsis and three dots.
    if "..." in p and "…" not in p:
        p = p.replace("...", "…")
    if "…" in p:
        pre, suf = p.split("…", 1)
        pre = pre.strip()
        suf = suf.strip()
        if pre and not k.startswith(pre):
            return False
        if suf and not k.endswith(suf):
            return False
        return True
    return _mask(k) == p

def _sha256_hex(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _state_path_default() -> Path:
    return secrets_root() / "fireworks_script_keys_state.json"


def _state_path_default_for_pool(pool: str) -> Path:
    p = str(pool or "").strip().lower()
    if p == "script":
        return secrets_root() / "fireworks_script_keys_state.json"
    if p == "image":
        return secrets_root() / "fireworks_image_keys_state.json"
    raise SystemExit(f"invalid --pool: {pool!r} (expected: script|image)")


def _quarantine_path_default_for_pool(pool: str) -> Path:
    p = str(pool or "").strip().lower()
    if p == "script":
        return secrets_root() / "fireworks_script_keys.quarantine.txt"
    if p == "image":
        return secrets_root() / "fireworks_image_keys.quarantine.txt"
    raise SystemExit(f"invalid --pool: {pool!r} (expected: script|image)")


def _state_path_from_env() -> Path:
    raw = (os.getenv("FIREWORKS_SCRIPT_KEYS_STATE_FILE") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return _state_path_default()


def _state_path_from_env_for_pool(pool: str) -> Path:
    p = str(pool or "").strip().lower()
    if p == "script":
        raw = (os.getenv("FIREWORKS_SCRIPT_KEYS_STATE_FILE") or "").strip()
        if raw:
            return Path(raw).expanduser().resolve()
        return _state_path_default_for_pool("script")
    if p == "image":
        raw = (os.getenv("FIREWORKS_IMAGE_KEYS_STATE_FILE") or "").strip()
        if raw:
            return Path(raw).expanduser().resolve()
        return _state_path_default_for_pool("image")
    raise SystemExit(f"invalid --pool: {pool!r} (expected: script|image)")


def _load_state(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "updated_at": None, "keys": {}}
    try:
        obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {"version": 1, "updated_at": None, "keys": {}}
    if not isinstance(obj, dict):
        return {"version": 1, "updated_at": None, "keys": {}}
    if not isinstance(obj.get("keys"), dict):
        obj["keys"] = {}
    obj.setdefault("version", 1)
    obj.setdefault("updated_at", None)
    return obj


def _write_state(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    obj["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def _update_state_for_key(state: dict, key: str, *, status: str, http_status: int | None, ratelimit: dict | None) -> None:
    keys_obj = state.get("keys")
    if not isinstance(keys_obj, dict):
        keys_obj = {}
        state["keys"] = keys_obj
    fp = _sha256_hex(key)
    keys_obj[fp] = {
        "status": str(status or "unknown"),
        "last_checked_at": datetime.now(timezone.utc).isoformat(),
        "last_http_status": int(http_status) if isinstance(http_status, int) else None,
        "ratelimit": ratelimit if isinstance(ratelimit, dict) and ratelimit else None,
    }


def _primary_key_from_env() -> str:
    return (os.getenv("FIREWORKS_SCRIPT") or os.getenv("FIREWORKS_SCRIPT_API_KEY") or "").strip()


def _primary_key_from_env_for_pool(pool: str) -> str:
    p = str(pool or "").strip().lower()
    if p == "script":
        return _primary_key_from_env()
    if p == "image":
        return (os.getenv("FIREWORKS_IMAGE") or os.getenv("FIREWORKS_IMAGE_API_KEY") or "").strip()
    raise SystemExit(f"invalid --pool: {pool!r} (expected: script|image)")


def _iter_keys_for_ops(keyring_path: Path) -> List[str]:
    primary = _primary_key_from_env()
    keys = [primary] if primary else []
    keys.extend(_read_keys(keyring_path))
    return _dedupe_keep_order([k for k in keys if k])


def _iter_keys_for_ops_for_pool(keyring_path: Path, pool: str) -> List[str]:
    primary = _primary_key_from_env_for_pool(pool)
    keys = [primary] if primary else []
    keys.extend(_read_keys(keyring_path))
    return _dedupe_keep_order([k for k in keys if k])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pool",
        type=str,
        default="script",
        choices=["script", "image"],
        help="key pool to manage: script (LLM) | image (workflow/image)",
    )
    parser.add_argument(
        "--path",
        type=str,
        default="",
        help="keyring file path (default depends on --pool)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("path", help="print keyring path (no keys)")

    sub.add_parser("init", help="create keyring file if missing (no keys)")

    p_add = sub.add_parser("add", help="add one key (no full key printed)")
    p_add.add_argument("--key", type=str, default="", help="key value (or read from stdin)")

    p_list = sub.add_parser("list", help="list key count (optionally masked)")
    p_list.add_argument("--show-masked", action="store_true", help="print masked keys (prefix…suffix)")

    p_check = sub.add_parser(
        "check",
        help="probe keys and update state (models=token-free; chat=1-token inference)",
    )
    p_check.add_argument("--limit", type=int, default=0, help="check only first N keys (0=all)")
    p_check.add_argument("--show-masked", action="store_true", help="print masked keys and status")
    p_check.add_argument(
        "--mode",
        type=str,
        default="models",
        choices=["models", "chat"],
        help="probe mode: models (GET /models; token-free) | chat (POST /chat/completions; small spend)",
    )
    p_check.add_argument(
        "--model",
        type=str,
        default="",
        help="model id for --mode chat (default: accounts/fireworks/models/glm-4p7)",
    )

    p_quar = sub.add_parser("quarantine", help="move unusable keys from keyring to a quarantine file")
    p_quar.add_argument(
        "--status",
        type=str,
        default="invalid,exhausted,suspended",
        help="comma-separated statuses to quarantine (based on state file)",
    )
    p_quar.add_argument(
        "--quarantine-path",
        type=str,
        default="",
        help="quarantine file path (default depends on --pool)",
    )
    p_quar.add_argument(
        "--match",
        action="append",
        default=[],
        help="also quarantine keys matching pattern (e.g. fw_1234…abcd or fw_1234...abcd); repeatable",
    )
    p_quar.add_argument("--dry-run", action="store_true", help="do not write files")
    p_quar.add_argument("--show-masked", action="store_true", help="print masked moved keys")

    p_restore = sub.add_parser("restore", help="restore keys from quarantine back into keyring")
    p_restore.add_argument(
        "--quarantine-path",
        type=str,
        default="",
        help="quarantine file path (default depends on --pool)",
    )
    p_restore.add_argument("--limit", type=int, default=0, help="restore only first N keys (0=all)")
    p_restore.add_argument(
        "--match",
        action="append",
        default=[],
        help="restore keys matching pattern (e.g. fw_1234…abcd or fw_1234...abcd); repeatable",
    )
    p_restore.add_argument("--dry-run", action="store_true", help="do not write files")
    p_restore.add_argument("--show-masked", action="store_true", help="print masked restored keys")

    p_mig = sub.add_parser("migrate-from-scratch", help="one-time import from legacy memo into keyring")
    p_mig.add_argument("--src", type=str, default="", help="legacy source path (default: workspaces/_scratch/fireworks_apiメモ)")

    args = parser.parse_args()
    pool = str(getattr(args, "pool", "script") or "script").strip().lower()
    path = (
        Path(args.path).expanduser().resolve()
        if str(args.path).strip()
        else _default_keyring_path_for_pool(pool)
    )

    if args.cmd == "path":
        print(str(path))
        return

    if args.cmd == "init":
        if not path.exists():
            _write_keys(path, [])
        print(f"ok: {path} (exists={path.exists()})")
        return

    if args.cmd == "add":
        key = str(getattr(args, "key", "") or "").strip()
        if not key:
            try:
                key = (os.sys.stdin.read() or "").strip()
            except Exception:
                key = ""
        parsed = _parse_keys(key)
        if len(parsed) != 1:
            raise SystemExit("invalid key: expected a single ASCII token (no spaces)")
        key = parsed[0]

        existing = _read_keys(path)
        merged = _dedupe_keep_order([*existing, key])
        if merged != existing:
            _write_keys(path, merged)
        print(f"ok: added (total={len(merged)})")
        return

    if args.cmd == "list":
        keys = _read_keys(path)
        state_path = _state_path_from_env_for_pool(pool)
        state = _load_state(state_path)
        states = state.get("keys") if isinstance(state.get("keys"), dict) else {}
        counts: dict[str, int] = {}
        for k in keys:
            ent = states.get(_sha256_hex(k)) if isinstance(states.get(_sha256_hex(k)), dict) else {}
            st = str((ent or {}).get("status") or "unknown")
            counts[st] = counts.get(st, 0) + 1

        counts_txt = " ".join([f"{k}={v}" for k, v in sorted(counts.items())])
        print(f"count={len(keys)} path={path} state={state_path} {counts_txt}".strip())
        if getattr(args, "show_masked", False):
            for k in keys:
                ent = states.get(_sha256_hex(k)) if isinstance(states.get(_sha256_hex(k)), dict) else {}
                st = str((ent or {}).get("status") or "unknown")
                hs = (ent or {}).get("last_http_status")
                tail = f" http={hs}" if hs is not None else ""
                print(f"{_mask(k)}\t{st}{tail}")
        return

    if args.cmd == "check":
        try:
            import requests  # type: ignore
        except Exception as exc:
            raise SystemExit(f"requests is required for check: {exc}") from exc

        keys = _iter_keys_for_ops_for_pool(path, pool)
        if not keys:
            raise SystemExit(
                "no keys found (set FIREWORKS_SCRIPT/FIREWORKS_IMAGE or add to keyring file)"
            )
        limit = int(getattr(args, "limit", 0) or 0)
        if limit > 0:
            keys = keys[:limit]

        state_path = _state_path_from_env_for_pool(pool)
        state = _load_state(state_path)

        mode = str(getattr(args, "mode", "models") or "models").strip().lower()
        endpoint = (
            "https://api.fireworks.ai/inference/v1/models"
            if mode == "models"
            else "https://api.fireworks.ai/inference/v1/chat/completions"
        )
        model = str(getattr(args, "model", "") or "").strip() or "accounts/fireworks/models/glm-4p7"
        payload = {"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1}

        summary = {"ok": 0, "invalid": 0, "exhausted": 0, "suspended": 0, "error": 0}
        rows: List[str] = []
        for k in keys:
            status = "unknown"
            http_status = None
            ratelimit = None
            try:
                if mode == "models":
                    r = requests.get(endpoint, headers={"Authorization": f"Bearer {k}"}, timeout=20)
                else:
                    r = requests.post(
                        endpoint,
                        headers={"Authorization": f"Bearer {k}"},
                        json=payload,
                        timeout=20,
                    )
                http_status = int(r.status_code)
                if r.status_code == 200:
                    status = "ok"
                    ratelimit = {
                        "limit_requests": r.headers.get("x-ratelimit-limit-requests"),
                        "remaining_requests": r.headers.get("x-ratelimit-remaining-requests"),
                        "limit_tokens_prompt": r.headers.get("x-ratelimit-limit-tokens-prompt"),
                        "remaining_tokens_prompt": r.headers.get("x-ratelimit-remaining-tokens-prompt"),
                        "limit_tokens_generated": r.headers.get("x-ratelimit-limit-tokens-generated"),
                        "remaining_tokens_generated": r.headers.get("x-ratelimit-remaining-tokens-generated"),
                        "over_limit": r.headers.get("x-ratelimit-over-limit"),
                    }
                elif r.status_code == 401:
                    status = "invalid"
                elif r.status_code == 402:
                    status = "exhausted"
                elif r.status_code == 412:
                    status = "suspended"
                else:
                    status = "error"
            except Exception:
                status = "error"
            summary[status] = summary.get(status, 0) + 1
            _update_state_for_key(state, k, status=status, http_status=http_status, ratelimit=ratelimit)
            if getattr(args, "show_masked", False):
                rows.append(f"{_mask(k)}\t{status}\thttp={http_status}")

        _write_state(state_path, state)
        print(
            f"ok={summary.get('ok',0)} exhausted={summary.get('exhausted',0)} "
            f"invalid={summary.get('invalid',0)} suspended={summary.get('suspended',0)} "
            f"error={summary.get('error',0)} total={len(keys)} state={state_path} mode={mode}"
        )
        for line in rows:
            print(line)
        return

    if args.cmd == "quarantine":
        statuses = {
            s.strip().lower()
            for s in str(getattr(args, "status", "") or "").split(",")
            if s.strip()
        }
        match_patterns = [str(p).strip() for p in (getattr(args, "match", []) or []) if str(p).strip()]
        qpath = (
            Path(str(getattr(args, "quarantine_path", "") or "").strip()).expanduser().resolve()
            if str(getattr(args, "quarantine_path", "") or "").strip()
            else _quarantine_path_default_for_pool(pool)
        )
        if qpath == path:
            raise SystemExit("--quarantine-path must be different from --path")

        keys = _read_keys(path)
        if not keys:
            print(f"ok: nothing to quarantine (keyring empty): {path}")
            return

        state_path = _state_path_from_env_for_pool(pool)
        state = _load_state(state_path)
        states = state.get("keys") if isinstance(state.get("keys"), dict) else {}

        moved: List[str] = []
        kept: List[str] = []
        for k in keys:
            ent = states.get(_sha256_hex(k)) if isinstance(states.get(_sha256_hex(k)), dict) else {}
            st = str((ent or {}).get("status") or "unknown").strip().lower()
            force = any(_matches_key(k, pat) for pat in match_patterns) if match_patterns else False
            if force or st in statuses:
                moved.append(k)
            else:
                kept.append(k)

        if not moved:
            tail = f" matches={match_patterns}" if match_patterns else ""
            print(f"ok: no keys matched statuses={sorted(statuses)} (kept={len(kept)}): {path}{tail}")
            return

        existing_q = _read_keys(qpath)
        merged_q = _dedupe_keep_order([*existing_q, *moved])

        dry_run = bool(getattr(args, "dry_run", False))
        if not dry_run:
            _write_keys(path, kept)
            _write_keys(qpath, merged_q)

        print(
            f"ok: quarantined={len(moved)} kept={len(kept)} quarantine_total={len(merged_q)} "
            f"statuses={sorted(statuses)} dry_run={dry_run} keyring={path} quarantine={qpath}"
        )
        if getattr(args, "show_masked", False):
            for k in moved:
                ent = states.get(_sha256_hex(k)) if isinstance(states.get(_sha256_hex(k)), dict) else {}
                st = str((ent or {}).get("status") or "unknown")
                hs = (ent or {}).get("last_http_status")
                tail = f" http={hs}" if hs is not None else ""
                print(f"{_mask(k)}\t{st}{tail}")
        return

    if args.cmd == "restore":
        qpath = (
            Path(str(getattr(args, "quarantine_path", "") or "").strip()).expanduser().resolve()
            if str(getattr(args, "quarantine_path", "") or "").strip()
            else _quarantine_path_default_for_pool(pool)
        )
        if qpath == path:
            raise SystemExit("--quarantine-path must be different from --path")

        qkeys = _read_keys(qpath)
        if not qkeys:
            print(f"ok: quarantine empty: {qpath}")
            return

        match_patterns = [str(p).strip() for p in (getattr(args, "match", []) or []) if str(p).strip()]
        limit = int(getattr(args, "limit", 0) or 0)
        if match_patterns:
            restore_keys = [k for k in qkeys if any(_matches_key(k, pat) for pat in match_patterns)]
            keep_q = [k for k in qkeys if k not in set(restore_keys)]
        else:
            restore_keys = qkeys[:limit] if limit > 0 else list(qkeys)
            keep_q = qkeys[len(restore_keys) :]

        active = _read_keys(path)
        merged = _dedupe_keep_order([*active, *restore_keys])

        dry_run = bool(getattr(args, "dry_run", False))
        if not dry_run:
            _write_keys(path, merged)
            _write_keys(qpath, keep_q)

        print(
            f"ok: restored={len(restore_keys)} active_total={len(merged)} "
            f"quarantine_remaining={len(keep_q)} dry_run={dry_run} keyring={path} quarantine={qpath}"
        )
        if getattr(args, "show_masked", False):
            for k in restore_keys:
                print(_mask(k))
        return

    if args.cmd == "migrate-from-scratch":
        src = (
            Path(str(getattr(args, "src", "") or "").strip()).expanduser().resolve()
            if str(getattr(args, "src", "") or "").strip()
            else _legacy_memo_path()
        )
        if not src.exists():
            raise SystemExit(f"legacy memo not found: {src}")
        try:
            memo_text = src.read_text(encoding="utf-8", errors="replace")
        except Exception:
            memo_text = ""
        extracted = _extract_fw_keys_anywhere(memo_text)
        qpath = _quarantine_path_default_for_pool(pool)
        qkeys = set(_read_keys(qpath))
        src_keys = [k for k in extracted if k not in qkeys]
        skipped_quarantined = max(0, len(extracted) - len(src_keys))
        dst_keys = _read_keys(path)
        merged = _dedupe_keep_order([*dst_keys, *src_keys])
        if merged != dst_keys:
            _write_keys(path, merged)
        print(
            f"ok: extracted={len(extracted)} imported={len(src_keys)} skipped_quarantined={skipped_quarantined} "
            f"total={len(merged)} from {src} quarantine={qpath}"
        )
        return

    raise SystemExit(f"unknown command: {args.cmd}")


if __name__ == "__main__":
    main()

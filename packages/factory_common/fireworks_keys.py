from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from factory_common.paths import repo_root, secrets_root

_FW_KEY_RE = re.compile(r"^fw_[A-Za-z0-9_-]{10,}$")
_RR_LOCK = threading.Lock()
_RR_CURSOR_BY_POOL: Dict[str, int] = {}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_hex(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def mask_key(key: str) -> str:
    k = str(key or "")
    if len(k) <= 8:
        return "*" * len(k)
    return f"{k[:4]}…{k[-4:]}"


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
    keys: List[str] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            _left, right = line.split("=", 1)
            line = right.strip()
        if "#" in line:
            line = line.split("#", 1)[0].strip()
        line = line.strip().strip("'\"")
        if " " in line or "\t" in line:
            continue
        if not all(ord(ch) < 128 for ch in line):
            continue
        if not _FW_KEY_RE.match(line):
            continue
        keys.append(line)
    return _dedupe_keep_order(keys)


def _read_keys_file(path: Path) -> List[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    return _parse_keys(text)


def _write_keys_file(path: Path, keys: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(keys) + ("\n" if keys else "")
    path.write_text(content, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def _pool_slug(pool: str) -> str:
    p = str(pool or "").strip().lower()
    if p in {"script", "scripts", "llm"}:
        return "script"
    if p in {"image", "images"}:
        return "image"
    raise ValueError(f"unknown fireworks key pool: {pool!r} (expected: script|image)")


def _pool_env(pool: str, *, key: str) -> str:
    p = _pool_slug(pool)
    if p == "script":
        table = {
            "primary": "FIREWORKS_SCRIPT",
            "primary_alias": "FIREWORKS_SCRIPT_API_KEY",
            "keys_inline": "FIREWORKS_SCRIPT_KEYS",
            "keys_file": "FIREWORKS_SCRIPT_KEYS_FILE",
            "state_file": "FIREWORKS_SCRIPT_KEYS_STATE_FILE",
        }
    else:
        table = {
            "primary": "FIREWORKS_IMAGE",
            "primary_alias": "FIREWORKS_IMAGE_API_KEY",
            "keys_inline": "FIREWORKS_IMAGE_KEYS",
            "keys_file": "FIREWORKS_IMAGE_KEYS_FILE",
            "state_file": "FIREWORKS_IMAGE_KEYS_STATE_FILE",
        }
    if key not in table:
        raise KeyError(key)
    return table[key]


def _resolve_env_path(raw: str) -> Path:
    """
    Interpret env-provided paths deterministically.

    Many tools in this repo run with different current working directories.
    If operators provide relative paths via env vars, resolving them against
    CWD becomes unstable. We therefore resolve relative paths against the repo
    root.
    """
    p = Path(str(raw or "")).expanduser()
    if not p.is_absolute():
        p = repo_root() / p
    return p.resolve()


def keyring_path(pool: str) -> Path:
    p = _pool_slug(pool)
    raw = (os.getenv(_pool_env(p, key="keys_file")) or "").strip()
    if raw:
        return _resolve_env_path(raw)
    return secrets_root() / f"fireworks_{p}_keys.txt"


def state_path(pool: str) -> Path:
    p = _pool_slug(pool)
    raw = (os.getenv(_pool_env(p, key="state_file")) or "").strip()
    if raw:
        return _resolve_env_path(raw)
    return secrets_root() / f"fireworks_{p}_keys_state.json"


def lease_root_dir() -> Path:
    raw = (os.getenv("FIREWORKS_KEYS_LEASE_DIR") or "").strip()
    if raw:
        return _resolve_env_path(raw)
    return secrets_root() / "fireworks_key_leases"


def _lease_path(fp: str) -> Path:
    return lease_root_dir() / f"{fp}.json"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace") or "null")
    except Exception:
        return None


def _atomic_write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except Exception:
        pass
    tmp.replace(path)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def _lease_expired(obj: Dict[str, Any], *, now: float) -> bool:
    exp = obj.get("expires_at") if isinstance(obj, dict) else None
    if not isinstance(exp, (int, float)):
        return True
    return float(exp) <= now


def _pid_alive(pid: int) -> bool:
    """
    Best-effort liveness check for local processes.
    Returns True when the PID exists (even if we lack permission), otherwise False.
    """
    try:
        pid_i = int(pid)
    except Exception:
        return False
    if pid_i <= 1:
        return False
    try:
        os.kill(pid_i, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        # Unknown platforms / transient errors: assume alive to avoid stealing leases.
        return True
    return True


def _lease_owner_dead(obj: Dict[str, Any]) -> bool:
    """
    Treat a lease as stale when:
    - it is owned by a PID on *this host*, and
    - that PID is no longer alive.
    """
    if not isinstance(obj, dict):
        return False
    host = str(obj.get("host") or "").strip()
    if host and host != socket.gethostname():
        return False
    pid = obj.get("pid")
    if not isinstance(pid, int):
        return False
    return not _pid_alive(pid)


def _agent_name() -> str:
    return (
        (os.getenv("LLM_AGENT_NAME") or "").strip()
        or (os.getenv("YTM_AGENT_NAME") or "").strip()
        or (os.getenv("USER") or "").strip()
        or "unknown"
    )


@dataclass(frozen=True)
class FireworksKeyLease:
    pool: str
    key_fp: str
    lease_id: str
    key: str
    acquired_at: str
    expires_at: str

    def masked_key(self) -> str:
        return mask_key(self.key)


def renew_lease(lease: FireworksKeyLease, *, ttl_sec: int) -> bool:
    """
    Extend the lease TTL if this process still owns it.
    Returns True when renewed, False when lease is missing or owned by someone else.
    """
    fp = str(getattr(lease, "key_fp", "") or "").strip()
    lease_id = str(getattr(lease, "lease_id", "") or "").strip()
    if not fp or not lease_id:
        return False
    path = _lease_path(fp)
    obj = _read_json(path)
    if not isinstance(obj, dict):
        return False
    if str(obj.get("lease_id") or "") != lease_id:
        return False
    now = time.time()
    ttl = float(max(30, int(ttl_sec)))
    obj["expires_at"] = now + ttl
    try:
        _atomic_write_json(path, obj)
    except Exception:
        return False
    return True


def list_active_leases() -> List[Dict[str, Any]]:
    root = lease_root_dir()
    if not root.exists():
        return []
    now = time.time()
    out: List[Dict[str, Any]] = []
    for p in sorted(root.glob("*.json")):
        obj = _read_json(p)
        if not isinstance(obj, dict):
            continue
        if _lease_expired(obj, now=now):
            continue
        obj2 = dict(obj)
        obj2["path"] = str(p)
        out.append(obj2)
    return out


def _try_acquire_lease(
    *,
    pool: str,
    key: str,
    ttl_sec: int,
    purpose: str,
) -> Optional[FireworksKeyLease]:
    k = str(key or "").strip()
    if not _FW_KEY_RE.match(k):
        return None

    now = time.time()
    ttl = float(max(30, int(ttl_sec)))
    fp = _sha256_hex(k)
    path = _lease_path(fp)

    lease_id = os.urandom(16).hex()
    payload: Dict[str, Any] = {
        "schema": "ytm.fireworks_key_lease.v1",
        "pool": _pool_slug(pool),
        "key_fp": fp,
        "lease_id": lease_id,
        "pid": int(os.getpid()),
        "host": socket.gethostname(),
        "agent": _agent_name(),
        "purpose": str(purpose or ""),
        "acquired_at": now,
        "expires_at": now + ttl,
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        obj = _read_json(path)
        if isinstance(obj, dict) and not _lease_expired(obj, now=now) and not _lease_owner_dead(obj):
            return None
        # stale → reclaim
        try:
            path.unlink()
        except Exception:
            # Some environments restrict unlink but allow rename; keep an audit trail and proceed.
            try:
                suffix = f"stale.{int(now)}.{os.getpid()}.{os.urandom(4).hex()}"
                path.replace(path.with_name(path.name + f".{suffix}"))
            except Exception:
                return None
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except Exception:
            return None
    except Exception:
        return None

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    except Exception:
        try:
            path.unlink()
        except Exception:
            pass
        return None

    return FireworksKeyLease(
        pool=_pool_slug(pool),
        key_fp=fp,
        lease_id=lease_id,
        key=k,
        acquired_at=_utc_now_iso(),
        expires_at=datetime.fromtimestamp(now + ttl, tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    )


def release_lease(lease: FireworksKeyLease) -> None:
    fp = str(getattr(lease, "key_fp", "") or "").strip()
    lease_id = str(getattr(lease, "lease_id", "") or "").strip()
    if not fp or not lease_id:
        return
    path = _lease_path(fp)
    obj = _read_json(path)
    if not isinstance(obj, dict):
        try:
            path.unlink()
        except Exception:
            pass
        return
    if str(obj.get("lease_id") or "") != lease_id:
        return
    try:
        path.unlink()
    except Exception:
        # Some environments restrict unlink but allow rename; keep an audit trail and avoid blocking future leases.
        try:
            now = time.time()
            suffix = f"released.{int(now)}.{os.getpid()}.{os.urandom(4).hex()}"
            path.replace(path.with_name(path.name + f".{suffix}"))
        except Exception:
            return


def _load_state(pool: str) -> Dict[str, Dict[str, Any]]:
    path = state_path(pool)
    if not path.exists():
        return {}
    obj = _read_json(path)
    if not isinstance(obj, dict):
        return {}
    keys_obj = obj.get("keys")
    return keys_obj if isinstance(keys_obj, dict) else {}


def _update_state(
    pool: str,
    *,
    key: str,
    status: str,
    http_status: Optional[int],
    ratelimit: Optional[Dict[str, Any]] = None,
    note: str = "",
) -> None:
    k = str(key or "").strip()
    if not k:
        return
    fp = _sha256_hex(k)
    path = state_path(pool)
    obj = _read_json(path)
    if not isinstance(obj, dict):
        obj = {}
    obj.setdefault("version", 1)
    keys_obj = obj.get("keys")
    if not isinstance(keys_obj, dict):
        keys_obj = {}
        obj["keys"] = keys_obj
    keys_obj[fp] = {
        "status": str(status or "unknown"),
        "last_checked_at": _utc_now_iso(),
        "last_http_status": int(http_status) if isinstance(http_status, int) else None,
        "note": str(note or ""),
        "ratelimit": ratelimit if isinstance(ratelimit, dict) and ratelimit else None,
    }
    obj["updated_at"] = _utc_now_iso()
    try:
        _atomic_write_json(path, obj)
    except Exception:
        return


def record_key_status(
    pool: str,
    *,
    key: str,
    status: str,
    http_status: Optional[int],
    note: str = "",
    ratelimit: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Persist key health info (never prints keys).
    """
    try:
        _update_state(
            pool,
            key=key,
            status=status,
            http_status=http_status,
            ratelimit=ratelimit,
            note=note,
        )
    except Exception:
        return


def purge_key_from_keyring(pool: str, *, key: str) -> bool:
    """
    Physically remove a key from the pool keyring file (never prints keys).
    Returns True when at least one line was removed.
    """
    k = str(key or "").strip()
    if not _FW_KEY_RE.match(k):
        return False

    p = _pool_slug(pool)
    path = keyring_path(p)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return False
    except Exception:
        return False

    def _extract_key_from_raw_line(raw: str) -> str:
        try:
            line = str(raw or "").strip()
            if not line or line.startswith("#"):
                return ""
            if "=" in line:
                _left, right = line.split("=", 1)
                line = right.strip()
            if "#" in line:
                line = line.split("#", 1)[0].strip()
            line = line.strip().strip("'\"")
            if " " in line or "\t" in line:
                return ""
            if not all(ord(ch) < 128 for ch in line):
                return ""
            if not _FW_KEY_RE.match(line):
                return ""
            return line
        except Exception:
            return ""

    removed = 0
    kept: List[str] = []
    for raw in text.splitlines(keepends=True):
        if _extract_key_from_raw_line(raw) == k:
            removed += 1
            continue
        kept.append(raw)

    if removed <= 0:
        return False

    out = "".join(kept)
    if out and not out.endswith("\n"):
        out += "\n"

    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(out, encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except Exception:
            pass
        tmp.replace(path)
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
    except Exception:
        return False

    return True


def probe_key(key: str) -> Tuple[str, Optional[int], Optional[Dict[str, Any]]]:
    """
    Token-free liveness/credit probe (does not call an LLM).
    Returns: (status, http_status, ratelimit_headers)

    Status:
    - ok: HTTP 200
    - invalid: HTTP 401
    - exhausted: HTTP 402
    - suspended: HTTP 412
    - error: anything else / network errors
    """
    k = str(key or "").strip()
    if not k:
        return ("error", None, None)
    url = "https://api.fireworks.ai/inference/v1/models"
    try:
        r = requests.get(url, headers={"Authorization": f"Bearer {k}"}, timeout=20)
    except Exception:
        return ("error", None, None)

    hs = int(r.status_code)
    if hs == 200:
        status = "ok"
    elif hs == 401:
        status = "invalid"
    elif hs == 402:
        status = "exhausted"
    elif hs == 412:
        status = "suspended"
    else:
        status = "error"

    ratelimit = None
    if hs == 200:
        ratelimit = {
            "limit_requests": r.headers.get("x-ratelimit-limit-requests"),
            "remaining_requests": r.headers.get("x-ratelimit-remaining-requests"),
            "limit_tokens_prompt": r.headers.get("x-ratelimit-limit-tokens-prompt"),
            "remaining_tokens_prompt": r.headers.get("x-ratelimit-remaining-tokens-prompt"),
            "limit_tokens_generated": r.headers.get("x-ratelimit-limit-tokens-generated"),
            "remaining_tokens_generated": r.headers.get("x-ratelimit-remaining-tokens-generated"),
            "over_limit": r.headers.get("x-ratelimit-over-limit"),
        }
    return (status, hs, ratelimit)


def candidate_keys(pool: str) -> List[str]:
    """
    Return candidate keys for the given pool (primary env first, then file/inline keys).
    Never prints keys.
    """
    p = _pool_slug(pool)
    primary_env = _pool_env(p, key="primary")
    alias_env = _pool_env(p, key="primary_alias")
    primary = (os.getenv(primary_env) or os.getenv(alias_env) or "").strip()
    # Legacy/compat: some operators still set FIREWORKS_API_KEY expecting it to apply to image.
    # We intentionally do NOT apply this to the script pool to avoid accidental cross-pool sharing.
    if not primary and p == "image":
        primary = (os.getenv("FIREWORKS_API_KEY") or "").strip()

    keys: List[str] = []
    if primary and _FW_KEY_RE.match(primary):
        keys.append(primary)

    raw_list = (os.getenv(_pool_env(p, key="keys_inline")) or "").strip()
    if raw_list:
        for part in raw_list.split(","):
            tok = part.strip()
            if tok and _FW_KEY_RE.match(tok):
                keys.append(tok)

    keys.extend(_read_keys_file(keyring_path(p)))
    return _dedupe_keep_order(keys)


def acquire_key(
    pool: str,
    *,
    purpose: str,
    ttl_sec: int = 1800,
    preflight: bool = True,
    allow_recheck_exhausted: bool = True,
) -> Optional[FireworksKeyLease]:
    """
    Acquire an exclusive lease for a usable key.

    - Honors pool separation (script/image)
    - Enforces global exclusivity by key fingerprint
    - Optionally performs a token-free preflight probe to avoid selecting exhausted keys
    """
    p = _pool_slug(pool)
    keys = candidate_keys(p)
    if not keys:
        return None

    st = _load_state(p)

    def _status_rank(status: str) -> int:
        s = str(status or "").lower()
        if s == "ok":
            return 0
        if s in {"unknown", ""}:
            return 1
        if s in {"error", "suspended"}:
            return 2
        if s == "exhausted":
            return 3
        if s == "invalid":
            return 4
        return 5

    scored: List[Tuple[int, int, str]] = []
    for idx, k in enumerate(keys):
        fp = _sha256_hex(k)
        ent = st.get(fp) if isinstance(st.get(fp), dict) else {}
        s = str((ent or {}).get("status") or "unknown")
        rank = _status_rank(s)
        if rank == 3 and not allow_recheck_exhausted:
            continue
        scored.append((rank, idx, k))

    ordered = sorted(scored, key=lambda x: (x[0], x[1]))
    if not ordered:
        return None

    best_rank = ordered[0][0]
    best = [t for t in ordered if t[0] == best_rank]
    rest = [t for t in ordered if t[0] != best_rank]

    start = 0
    if len(best) > 1:
        with _RR_LOCK:
            cur = int(_RR_CURSOR_BY_POOL.get(p, 0))
            start = cur % len(best)
            _RR_CURSOR_BY_POOL[p] = cur + 1
    rotated = best[start:] + best[:start] + rest

    for _rank, _idx, k in rotated:
        lease = _try_acquire_lease(pool=p, key=k, ttl_sec=int(ttl_sec), purpose=purpose)
        if lease is None:
            continue
        if not preflight:
            return lease
        status, http_status, ratelimit = probe_key(k)
        _update_state(p, key=k, status=status, http_status=http_status, ratelimit=ratelimit)
        if status == "ok":
            return lease
        release_lease(lease)

    return None


def try_acquire_specific_key(
    pool: str,
    *,
    key: str,
    purpose: str,
    ttl_sec: int = 1800,
    preflight: bool = True,
) -> Optional[FireworksKeyLease]:
    """
    Attempt to lease a specific key (global exclusivity).
    Returns a lease only if acquired (and preflight passes when enabled).
    """
    p = _pool_slug(pool)
    k = str(key or "").strip()
    lease = _try_acquire_lease(pool=p, key=k, ttl_sec=int(ttl_sec), purpose=purpose)
    if lease is None:
        return None
    if not preflight:
        return lease
    status, http_status, ratelimit = probe_key(k)
    _update_state(p, key=k, status=status, http_status=http_status, ratelimit=ratelimit)
    if status == "ok":
        return lease
    release_lease(lease)
    return None

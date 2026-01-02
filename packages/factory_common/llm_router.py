import os
import re
import yaml
import time
import json
import hashlib
import logging
import inspect
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Union
from pathlib import Path
from dotenv import load_dotenv

from factory_common.llm_param_guard import sanitize_params
from factory_common.agent_mode import maybe_handle_agent_mode
from factory_common.llm_api_failover import maybe_failover_to_think
from factory_common.llm_api_cache import (
    cache_enabled_for_task as _api_cache_enabled_for_task,
    cache_path as _api_cache_path,
    make_task_id as _api_cache_task_id,
    read_cache as _api_cache_read,
    write_cache as _api_cache_write,
)
from factory_common.codex_exec_layer import try_codex_exec
from factory_common.paths import logs_root, repo_root, secrets_root

DEFAULT_FALLBACK_POLICY = {
    "transient_statuses": [429, 500, 502, 503, 504, 408],
    "retry_limit": 0,  # 0 means try all
    "backoff_sec": 1.0,
    "per_status_backoff": {},
    "per_status_retry": {},
    "max_total_attempts": 0,
    "max_total_wait_sec": 0,
}
TRANSIENT_STATUSES = set(DEFAULT_FALLBACK_POLICY["transient_statuses"])

# ---------------------------------------------------------------------------
# Fireworks key rotation (same provider, multi-key)
# ---------------------------------------------------------------------------

_FIREWORKS_API_KEY_RE = re.compile(r"^fw_[A-Za-z0-9_-]{10,}$")

def _env_truthy(name: str, default: str = "0") -> bool:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        raw = str(default or "").strip()
    raw = raw.lower()
    return raw not in {"", "0", "false", "no", "off"}


def _sha256_hex(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _fireworks_keys_state_file_default() -> Path:
    return secrets_root() / "fireworks_script_keys_state.json"


def _fireworks_keys_state_file_path() -> Path:
    raw = (os.getenv("FIREWORKS_SCRIPT_KEYS_STATE_FILE") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return _fireworks_keys_state_file_default()


def _load_fireworks_key_state() -> Dict[str, Dict[str, Any]]:
    """
    Load per-key state (no raw keys stored).

    Format:
      {
        "version": 1,
        "updated_at": "...",
        "keys": {
          "<sha256>": {"status":"ok|invalid|exhausted|unknown", "last_http_status": 200, ...}
        }
      }
    """
    path = _fireworks_keys_state_file_path()
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    keys_obj = obj.get("keys") if isinstance(obj, dict) else None
    return keys_obj if isinstance(keys_obj, dict) else {}


def _update_fireworks_key_state(key: str, *, status: str, http_status: Optional[int], note: str = "") -> None:
    """
    Persist per-key status without storing raw keys (sha256 only).
    """
    k = str(key or "").strip()
    if not k:
        return
    fp = _sha256_hex(k)
    path = _fireworks_keys_state_file_path()
    state: Dict[str, Any] = {"version": 1, "updated_at": datetime.now(timezone.utc).isoformat(), "keys": {}}
    if path.exists():
        try:
            prev = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(prev, dict):
                state["version"] = int(prev.get("version") or 1)
                state["keys"] = prev.get("keys") if isinstance(prev.get("keys"), dict) else {}
        except Exception:
            pass
    keys_obj: Dict[str, Any] = state.get("keys") if isinstance(state.get("keys"), dict) else {}
    keys_obj[fp] = {
        "status": str(status or "unknown"),
        "last_checked_at": datetime.now(timezone.utc).isoformat(),
        "last_http_status": int(http_status) if isinstance(http_status, int) else None,
        "note": str(note or ""),
    }
    state["keys"] = keys_obj
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
    except Exception:
        # Never fail the pipeline due to state persistence.
        return


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


def _read_fireworks_keys_file(path: Path) -> List[str]:
    """
    Read a Fireworks API key list file.

    Supported formats:
    - One key per line (recommended)
    - ENV-like: FIREWORKS_SCRIPT=... (value extracted)
    - Comments via leading '#'

    IMPORTANT: This function must never print keys.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []

    keys: List[str] = []
    for raw in text.splitlines():
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
        if not _FIREWORKS_API_KEY_RE.match(line):
            continue
        keys.append(line)
    return _dedupe_keep_order(keys)


def _fireworks_keys_file_default() -> Path:
    # Default secrets file (operator-local, untracked; NOT inside the repo).
    # Override with FIREWORKS_SCRIPT_KEYS_FILE if needed.
    return secrets_root() / "fireworks_script_keys.txt"


def _fireworks_key_candidates(primary_key: Optional[str]) -> List[str]:
    keys: List[str] = []
    if primary_key and _FIREWORKS_API_KEY_RE.match(str(primary_key).strip()):
        keys.append(str(primary_key).strip())

    # Optional comma-separated keys (no whitespace).
    raw_list = (os.getenv("FIREWORKS_SCRIPT_KEYS") or "").strip()
    if raw_list:
        for part in raw_list.split(","):
            tok = part.strip()
            if tok and _FIREWORKS_API_KEY_RE.match(tok):
                keys.append(tok)

    raw_file = (os.getenv("FIREWORKS_SCRIPT_KEYS_FILE") or "").strip()
    if raw_file:
        p = Path(raw_file).expanduser().resolve()
        keys.extend(_read_fireworks_keys_file(p))
    else:
        p = _fireworks_keys_file_default()
        if p.exists():
            keys.extend(_read_fireworks_keys_file(p))

    keys = _dedupe_keep_order(keys)

    # Default: skip keys already known to be exhausted/invalid (unless explicitly disabled).
    if _env_truthy("FIREWORKS_SCRIPT_KEYS_SKIP_EXHAUSTED", default="1"):
        st = _load_fireworks_key_state()
        if isinstance(st, dict) and st:
            filtered: List[str] = []
            for k in keys:
                fp = _sha256_hex(k)
                ent = st.get(fp) if isinstance(st.get(fp), dict) else {}
                s = str((ent or {}).get("status") or "").strip().lower()
                if s in {"exhausted", "invalid"}:
                    continue
                filtered.append(k)
            keys = filtered

    return keys

# HTTPステータスを例外から推測するための簡易ヘルパ
def _extract_status(exc: Exception) -> Optional[int]:
    for attr in ("http_status", "status_code", "status"):
        if hasattr(exc, attr):
            try:
                val = int(getattr(exc, attr))
                return val
            except Exception:
                continue
    resp = getattr(exc, "response", None)
    if resp is not None:
        for attr in ("status_code", "status"):
            if hasattr(resp, attr):
                try:
                    return int(getattr(resp, attr))
                except Exception:
                    continue
    return None


def _extract_request_id(result: Any) -> Optional[str]:
    for attr in ("id", "request_id"):
        if hasattr(result, attr):
            try:
                val = getattr(result, attr)
                if val:
                    return str(val)
            except Exception:
                continue
    resp = getattr(result, "response", None)
    if resp is not None:
        for attr in ("id", "request_id"):
            if hasattr(resp, attr):
                try:
                    val = getattr(resp, attr)
                    if val:
                        return str(val)
                except Exception:
                    continue
    return None


def _extract_finish_reason(result: Any) -> Optional[str]:
    """
    Extract finish_reason for OpenAI-compatible ChatCompletion responses.
    Returns None when unavailable.
    """
    try:
        choices = getattr(result, "choices", None)
        if choices:
            fr = getattr(choices[0], "finish_reason", None)
            if fr:
                return str(fr)
    except Exception:
        pass
    return None
# Try importing OpenAI
try:
    from openai import OpenAI, AzureOpenAI
except ImportError:
    OpenAI = None
    AzureOpenAI = None

# Try importing Gemini
try:
    import google.generativeai as genai
except ImportError:
    genai = None

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("LLMRouter")

PROJECT_ROOT = repo_root()
_BASE_CONFIG_PATH = PROJECT_ROOT / "configs" / "llm_router.yaml"
_LOCAL_CONFIG_PATH = PROJECT_ROOT / "configs" / "llm_router.local.yaml"
FALLBACK_POLICY_PATH = PROJECT_ROOT / "configs" / "llm_fallback_policy.yaml"
DEFAULT_LOG_PATH = logs_root() / "llm_usage.jsonl"
TASK_OVERRIDE_PATH = PROJECT_ROOT / "configs" / "llm_task_overrides.yaml"
ENV_PATH = PROJECT_ROOT / ".env"

LLM_TRACE_SCHEMA_V1 = "ytm.llm_trace.v1"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _trace_disabled() -> bool:
    val = (os.getenv("YTM_TRACE_LLM") or "").strip().lower()
    return val in ("0", "false", "no", "off")


def _trace_key() -> str:
    return (os.getenv("LLM_ROUTING_KEY") or os.getenv("YTM_TRACE_KEY") or "").strip()


def _safe_trace_key(key: str) -> str:
    k = (key or "").strip()
    if not k:
        return ""
    return re.sub(r"[^A-Za-z0-9_.\\-]+", "_", k)[:180]


def _repo_relpath_str(path: str) -> str:
    try:
        p = Path(path).expanduser().resolve()
        return str(p.relative_to(PROJECT_ROOT))
    except Exception:
        return str(path or "")


def _resolve_callsite() -> Dict[str, Any] | None:
    try:
        fr = inspect.currentframe()
        if fr is None:
            return None
        # Walk back until we exit this module.
        cur = fr
        for _ in range(0, 64):
            cur = cur.f_back  # type: ignore[assignment]
            if cur is None:
                return None
            filename = cur.f_code.co_filename
            if not filename.endswith("llm_router.py"):
                return {
                    "path": _repo_relpath_str(filename),
                    "line": int(cur.f_lineno),
                    "function": str(cur.f_code.co_name),
                }
    except Exception:
        return None
    return None


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for k, v in value.items():
            out[str(k)] = _json_safe(v)
        return out
    return str(value)

def _deep_merge_dict(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deep-merge dicts (override wins), keeping base keys when override is partial.

    This avoids config drift when `configs/llm_router.local.yaml` exists but omits newer sections.
    """
    out: Dict[str, Any] = dict(base or {})
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge_dict(out.get(k) or {}, v)  # type: ignore[arg-type]
        else:
            out[k] = v
    return out


def _append_llm_trace_event(event: Dict[str, Any]) -> None:
    if _trace_disabled():
        return
    key = _trace_key()
    always = (os.getenv("YTM_TRACE_LLM_ALWAYS") or "").strip().lower() in ("1", "true", "yes", "on")
    if not key and not always:
        return

    safe_key = _safe_trace_key(key) if key else "_global"
    try:
        out_dir = logs_root() / "traces" / "llm"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{safe_key}.jsonl"
        with out_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        # Best-effort only; tracing must never break production.
        pass


def _trace_llm_call(
    *,
    task: str,
    messages: List[Dict[str, str]],
    result: Dict[str, Any],
    request_options: Dict[str, Any],
) -> None:
    try:
        event: Dict[str, Any] = {
            "schema": LLM_TRACE_SCHEMA_V1,
            "generated_at": _utc_now_iso(),
            "trace_key": _trace_key() or None,
            "task": str(task),
            "provider": result.get("provider"),
            "model": result.get("model"),
            "request_id": result.get("request_id"),
            "chain": result.get("chain"),
            "latency_ms": result.get("latency_ms"),
            "finish_reason": result.get("finish_reason"),
            "routing": _json_safe(result.get("routing")),
            "cache": _json_safe(result.get("cache")),
            "usage": _json_safe(result.get("usage") or {}),
            "callsite": _resolve_callsite(),
            "request": _json_safe(request_options),
            "messages": _json_safe(messages),
        }
        _append_llm_trace_event(event)
    except Exception:
        pass

_OPENROUTER_REASONING_MODEL_ALLOWLIST_SUBSTR = {
    # OpenRouter "reasoning.enabled" is only forwarded for allowlisted models.
    # Keeping this strict prevents 400s when a task falls back to a non-reasoning model.
    "deepseek-v3.2-exp",
    "kimi-k2-thinking",
}

# Fireworks uses OpenAI-compatible model ids like:
#   accounts/fireworks/models/deepseek-v3p2
# This repo historically uses the OpenRouter id as the "logical" name:
#   deepseek/deepseek-v3.2-exp
# Keep configs stable by translating at the router boundary.
_FIREWORKS_LOGICAL_MODEL_ID_TO_MODEL_ID = {
    "deepseek/deepseek-v3.2-exp": "accounts/fireworks/models/deepseek-v3p2",
}
_FIREWORKS_MODEL_ID_TO_LOGICAL_MODEL_ID = {v: k for k, v in _FIREWORKS_LOGICAL_MODEL_ID_TO_MODEL_ID.items()}

_FIREWORKS_FINAL_MARKER = "<<<YTM_FINAL>>>"
_FIREWORKS_FINAL_MARKER_RE = re.compile(r"<{2,}YTM_FINAL>{2,}")
_FIREWORKS_REASONING_MIN_MAX_TOKENS = 4096


def _extract_after_fireworks_marker(text: str) -> str:
    """
    Fireworks "final marker" extraction (tolerant).

    Some models occasionally reproduce the marker with minor variations (e.g. `<<YTM_FINAL>>>`).
    We treat any `<{2,}YTM_FINAL>{2,}` occurrence as a marker and return the text after the last one.
    """
    s = str(text or "")
    if not s:
        return ""
    last = None
    for m in _FIREWORKS_FINAL_MARKER_RE.finditer(s):
        last = m
    if last is None:
        return s.strip()
    after = s[last.end() :].strip()
    if after:
        return after
    # Some models occasionally place the marker at the end; keep the content before it in that case.
    return s[: last.start()].strip()


def _fireworks_model_id_from_logical(model_id: str) -> str:
    mid = str(model_id or "").strip()
    return _FIREWORKS_LOGICAL_MODEL_ID_TO_MODEL_ID.get(mid, mid)


def _logical_model_id_from_fireworks(model_id: str) -> str:
    mid = str(model_id or "").strip()
    return _FIREWORKS_MODEL_ID_TO_LOGICAL_MODEL_ID.get(mid, mid)


def _strip_code_fences(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```"):
        import re

        s = re.sub(r"^```[a-zA-Z0-9_-]*\\n?", "", s).strip()
        if s.endswith("```"):
            s = s[: -3].rstrip()
    return s


def _extract_json_object_chunk(text: str) -> Optional[str]:
    """
    Best-effort extraction of a JSON object substring from a possibly noisy model output.
    Returns the *last* parseable JSON object (dict) found at top-level.
    """
    s = _strip_code_fences(text)
    if not s:
        return None

    candidates: list[str] = []
    depth = 0
    start: int | None = None
    in_string = False
    escape = False

    for i, ch in enumerate(s):
        if in_string:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            continue

        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
            continue

        if ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                chunk = s[start : i + 1].strip()
                try:
                    obj = json.loads(chunk)
                    if isinstance(obj, dict):
                        candidates.append(chunk)
                except Exception:
                    pass
                start = None

    if candidates:
        return candidates[-1]

    # Fallback: naive first..last braces (may still work when output is clean JSON).
    start_idx = s.find("{")
    end_idx = s.rfind("}")
    if start_idx < 0 or end_idx < 0 or end_idx <= start_idx:
        return None
    chunk = s[start_idx : end_idx + 1].strip()
    try:
        obj = json.loads(chunk)
        return chunk if isinstance(obj, dict) else None
    except Exception:
        return None


def _is_parseable_json_value(text: str) -> bool:
    """
    Best-effort: return True if the string contains a parseable JSON dict/list.

    Used to guard app-level cache hits for tasks that request JSON mode. If cached content is not
    parseable, it's almost always a poisoned cache entry (bad provider output) and should be ignored.
    """
    s = _strip_code_fences(text)
    if not s:
        return False
    decoder = json.JSONDecoder()
    for m in re.finditer(r"[\\[{]", s):
        try:
            obj, _end = decoder.raw_decode(s[m.start() :])
        except Exception:
            continue
        if isinstance(obj, (dict, list)):
            return True
    return False


def _extract_after_marker(text: str, marker: str) -> str:
    s = str(text or "")
    if not s:
        return ""
    if marker not in s:
        return s.strip()
    return s.split(marker)[-1].strip()


def _openrouter_model_allows_reasoning(model_name: str) -> bool:
    mn = str(model_name or "").strip().lower()
    if not mn:
        return False
    return any(tok in mn for tok in _OPENROUTER_REASONING_MODEL_ALLOWLIST_SUBSTR)


def _parse_ratio_env(name: str) -> Optional[float]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        val = float(raw)
    except Exception:
        return None
    if val <= 0:
        return 0.0
    if val >= 1:
        return 1.0
    return val


def _split_bucket(key: str) -> float:
    """
    Return a stable bucket in [0, 1) for a given key (no secrets; key is hashed).
    """
    digest = hashlib.sha1((key or "").encode("utf-8")).digest()
    n = int.from_bytes(digest[:2], "big")  # 0..65535
    return n / 65536.0

def _load_env_forced():
    """Load .env file and OVERWRITE existing env vars to ensure SSOT."""
    if ENV_PATH.exists():
        # Using python-dotenv with override=True
        load_dotenv(dotenv_path=ENV_PATH, override=True)
    else:
        logger.warning(f".env not found at {ENV_PATH}")

class LLMRouter:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(LLMRouter, cls).__new__(cls)
            cls._instance._initialized = False
            cls._instance.task_overrides = {}
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        
        _load_env_forced()
        self.config = self._load_config()
        self.fallback_policy = self._load_fallback_policy()
        self.task_overrides = self._load_task_overrides()
        self._setup_clients()
        if self.task_overrides is None:
            self.task_overrides = {}
        self._initialized = True

    def _load_config(self) -> Dict[str, Any]:
        if not _BASE_CONFIG_PATH.exists():
            raise FileNotFoundError(f"Router config not found at {_BASE_CONFIG_PATH}")
        base = yaml.safe_load(_BASE_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        if not isinstance(base, dict):
            base = {}
        if _LOCAL_CONFIG_PATH.exists():
            try:
                local = yaml.safe_load(_LOCAL_CONFIG_PATH.read_text(encoding="utf-8")) or {}
            except Exception:
                local = {}
            if isinstance(local, dict) and local:
                return _deep_merge_dict(base, local)
        return base

    def _log_usage(self, payload: Dict[str, Any]) -> None:
        if os.getenv("LLM_ROUTER_LOG_DISABLE") == "1":
            return
        log_path = Path(os.getenv("LLM_ROUTER_LOG_PATH") or DEFAULT_LOG_PATH)
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.debug(f"LLM usage log write failed: {e}")

    def _load_fallback_policy(self) -> Dict[str, Any]:
        policy = DEFAULT_FALLBACK_POLICY.copy()
        if FALLBACK_POLICY_PATH.exists():
            try:
                loaded = yaml.safe_load(FALLBACK_POLICY_PATH.read_text())
                if isinstance(loaded, dict):
                    policy.update({k: loaded.get(k, v) for k, v in policy.items()})
            except Exception as e:
                logger.warning(f"Failed to load fallback policy; using defaults. Error: {e}")
        return policy

    def _load_task_overrides(self) -> Dict[str, Any]:
        overrides: Dict[str, Any] = {}
        if TASK_OVERRIDE_PATH.exists():
            try:
                loaded = yaml.safe_load(TASK_OVERRIDE_PATH.read_text()) or {}
                if isinstance(loaded, dict):
                    overrides = loaded.get("tasks", {})
            except Exception as e:
                logger.warning(f"Failed to load task overrides; ignoring. Error: {e}")
        return overrides

    def _extract_usage(self, result: Any) -> Dict[str, Any]:
        """
        Providerごとの usage 情報を抽出して dict を返す。
        未対応/取得不可の場合は空 dict。
        """
        usage = {}
        # OpenAI/Azure client returns .usage on ChatCompletion
        if hasattr(result, "usage"):
            try:
                usage = {
                    "prompt_tokens": getattr(result.usage, "prompt_tokens", None),
                    "completion_tokens": getattr(result.usage, "completion_tokens", None),
                    "total_tokens": getattr(result.usage, "total_tokens", None),
                }
            except Exception:
                pass
        # Gemini: result may have usage_metadata on the response
        if hasattr(result, "usage_metadata"):
            try:
                meta = result.usage_metadata
                usage.update({
                    "prompt_tokens": getattr(meta, "prompt_token_count", None),
                    "completion_tokens": getattr(meta, "candidates_token_count", None),
                    "total_tokens": getattr(meta, "total_token_count", None),
                })
            except Exception:
                pass
        # Remove None-only entries
        usage = {k: v for k, v in usage.items() if v is not None}
        return usage

    def _setup_clients(self):
        self.clients = {}
        providers = self.config.get("providers", {})

        # Azure
        if "azure" in providers:
            p = providers["azure"]
            ep = os.getenv(p.get("env_endpoint"))
            key = os.getenv(p.get("env_api_key"))
            ver = p.get("default_api_version")
            if ep and key and AzureOpenAI:
                # Handle missing protocol in endpoint if common
                if not ep.startswith("http"):
                    ep = "https://" + ep
                
                # Fix: Strip trailing paths from endpoint for SDK
                # The AzureOpenAI client expects the base endpoint (e.g. https://foo.openai.azure.com/)
                # It appends /openai/deployments/... itself.
                # If users put full path in .env, we should clean it.
                if "/openai/" in ep:
                    ep = ep.split("/openai/")[0]
                
                self.clients["azure"] = AzureOpenAI(
                    api_key=key,
                    api_version=ver,
                    azure_endpoint=ep
                )

        # OpenRouter
        if "openrouter" in providers:
            p = providers["openrouter"]
            key = os.getenv(p.get("env_api_key"))
            base = p.get("base_url")
            if key and OpenAI:
                headers: Dict[str, str] = {}
                ref = (os.getenv("OPENROUTER_REFERRER") or "").strip()
                title = (os.getenv("OPENROUTER_TITLE") or "").strip()
                if ref:
                    headers["HTTP-Referer"] = ref
                if title:
                    headers["X-Title"] = title

                try:
                    self.clients["openrouter"] = OpenAI(
                        api_key=key,
                        base_url=base,
                        default_headers=headers or None,
                    )
                except TypeError:
                    self.clients["openrouter"] = OpenAI(
                        api_key=key,
                        base_url=base,
                    )

        # Fireworks (OpenAI-compatible)
        if "fireworks" in providers:
            p = providers["fireworks"]
            key_name = p.get("env_api_key")
            key = os.getenv(key_name)
            # Backward-compat env aliases (operator convenience):
            # - Prefer: FIREWORKS_SCRIPT
            # - Legacy: FIREWORKS_SCRIPT_API_KEY
            if not key and key_name == "FIREWORKS_SCRIPT":
                key = os.getenv("FIREWORKS_SCRIPT_API_KEY")
            if not key and key_name == "FIREWORKS_SCRIPT_API_KEY":
                key = os.getenv("FIREWORKS_SCRIPT")
            base = p.get("base_url")
            if base and OpenAI:
                # Fireworks key rotation (same provider only):
                # - Use the explicitly set key first (FIREWORKS_SCRIPT / FIREWORKS_SCRIPT_API_KEY).
                # - Optionally load additional keys from:
                #   - FIREWORKS_SCRIPT_KEYS (comma-separated)
                #   - FIREWORKS_SCRIPT_KEYS_FILE (one key per line)
                #   - ~/.ytm/secrets/fireworks_script_keys.txt (default)
                self._fireworks_keys = _fireworks_key_candidates(key)
                self._fireworks_key_index = 0
                self._fireworks_dead_keys = set()
                chosen = (self._fireworks_keys[0] if self._fireworks_keys else key)
                if chosen:
                    try:
                        self.clients["fireworks"] = OpenAI(api_key=chosen, base_url=base)
                    except TypeError:
                        self.clients["fireworks"] = OpenAI(api_key=chosen, base_url=str(base))

        # Gemini
        if "gemini" in providers:
            p = providers["gemini"]
            key = os.getenv(p.get("env_api_key"))
            if key and genai:
                genai.configure(api_key=key)
                self.clients["gemini"] = "configured" # Client is static

    def _fireworks_mark_current_key_dead(self, *, http_status: Optional[int] = None) -> None:
        keys = getattr(self, "_fireworks_keys", None)
        dead = getattr(self, "_fireworks_dead_keys", None)
        idx = getattr(self, "_fireworks_key_index", None)
        if not isinstance(keys, list) or not isinstance(dead, set) or not isinstance(idx, int):
            return
        if 0 <= idx < len(keys):
            k = keys[idx]
            dead.add(k)
            # Persist only the statuses that reliably mean "this key won't work now".
            if http_status == 401:
                _update_fireworks_key_state(k, status="invalid", http_status=http_status, note="401 unauthorized")
            elif http_status == 402:
                _update_fireworks_key_state(k, status="exhausted", http_status=http_status, note="402 payment required")

    def _fireworks_rotate_client(self) -> Optional[Any]:
        """
        Rotate to the next available Fireworks API key (same provider).
        Returns the new OpenAI client, or None if no key is available.
        """
        keys = getattr(self, "_fireworks_keys", None)
        dead = getattr(self, "_fireworks_dead_keys", None)
        idx = getattr(self, "_fireworks_key_index", None)
        if not (OpenAI and isinstance(keys, list) and keys and isinstance(dead, set) and isinstance(idx, int)):
            return None

        fw_conf = (self.config.get("providers", {}) or {}).get("fireworks", {}) if isinstance(self.config, dict) else {}
        base_url = fw_conf.get("base_url")
        if not base_url:
            return None

        for step in range(1, len(keys) + 1):
            ni = (idx + step) % len(keys)
            k = keys[ni]
            if not k or k in dead:
                continue
            try:
                new_client = OpenAI(api_key=k, base_url=base_url)
            except TypeError:
                new_client = OpenAI(api_key=k, base_url=str(base_url))
            self._fireworks_key_index = ni
            self.clients["fireworks"] = new_client
            return new_client
        return None

    def get_models_for_task(self, task: str, *, model_keys_override: Optional[List[str]] = None) -> List[str]:
        # Runtime overrides (CLI/UI):
        # - LLM_FORCE_MODELS="model_key1,model_key2"
        # - LLM_FORCE_TASK_MODELS_JSON='{"task_name":["model_key1","model_key2"]}'
        # These allow swapping models without editing router configs.
        models_conf = self.config.get("models", {}) or {}

        def _truthy_env(name: str) -> bool:
            return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "y", "on")

        def _filter_disallowed_models(task_name: str, candidates: List[str]) -> List[str]:
            # Hard safety rule:
            # - Never route script-writing tasks through Azure/OpenAI "GPT" models.
            #   (User requirement: gpt-5-mini must not touch scripts.)
            tn = str(task_name or "")
            if not tn.startswith("script_"):
                return candidates
            out: List[str] = []
            removed: List[str] = []
            for mk in candidates:
                conf = models_conf.get(mk) if isinstance(models_conf, dict) else None
                provider = str((conf or {}).get("provider") or "").strip().lower()
                if mk.startswith("azure_") or provider in {"azure", "openai"}:
                    removed.append(mk)
                    continue
                # Fireworks-only script policy (default):
                # - For any `script_*` task, do not attempt OpenRouter models unless explicitly allowed.
                # - This prevents accidental provider drift and matches the operational rule:
                #   "If Fireworks is down, STOP (do not fall back to OpenRouter for scripts)."
                if provider == "openrouter" and not _truthy_env("YTM_SCRIPT_ALLOW_OPENROUTER"):
                    removed.append(mk)
                    continue
                out.append(mk)
            if removed:
                logger.info("Router: filtered disallowed models for %s: %s", tn, removed)
            return out

        def _split_model_keys(raw: object) -> List[str]:
            if raw is None:
                return []
            if isinstance(raw, list):
                out: List[str] = []
                for item in raw:
                    tok = str(item).strip()
                    if tok:
                        out.append(tok)
                return out
            text = str(raw).strip()
            if not text:
                return []
            return [p.strip() for p in text.split(",") if p.strip()]

        def _resolve_model_key_alias(token: str) -> Optional[str]:
            """
            Best-effort: resolve non-key aliases to a configured model key.

            Supported aliases (for runtime overrides):
            - OpenRouter model id: "deepseek/deepseek-v3.2-exp"
            - Azure deployment: "gpt-5-mini"
            - Explicit provider prefix: "openrouter:deepseek/deepseek-v3.2-exp", "azure:gpt-5-mini"
            """

            raw = str(token or "").strip()
            if not raw:
                return None

            provider: Optional[str] = None
            model_id = raw
            if ":" in raw:
                left, right = raw.split(":", 1)
                left = left.strip().lower()
                right = right.strip()
                if left and right:
                    provider = left
                    model_id = right

            # Allow Fireworks model ids to resolve to the logical id stored in configs.
            # Example:
            #   accounts/fireworks/models/deepseek-v3p2 -> deepseek/deepseek-v3.2-exp
            model_id = _logical_model_id_from_fireworks(model_id)

            matches: List[str] = []
            for model_key, conf in models_conf.items():
                if not isinstance(conf, dict):
                    continue
                conf_provider = str(conf.get("provider") or "").strip().lower()
                if provider and conf_provider != provider:
                    continue
                deployment = conf.get("deployment")
                model_name = conf.get("model_name")
                if isinstance(deployment, str) and deployment.strip() == model_id:
                    matches.append(model_key)
                    continue
                if isinstance(model_name, str) and model_name.strip() == model_id:
                    matches.append(model_key)
                    continue

            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                logger.warning("LLM_FORCE_MODELS alias is ambiguous for %s: %s", raw, matches)
            return None

        def _normalize_forced_models(forced: List[str]) -> List[str]:
            out: List[str] = []
            seen: set[str] = set()
            for tok in forced:
                mk = tok if tok in models_conf else _resolve_model_key_alias(tok)
                if mk and mk not in seen:
                    out.append(mk)
                    seen.add(mk)
            return out

        if model_keys_override:
            forced = _split_model_keys(model_keys_override)
            forced_valid = _normalize_forced_models(forced)
            if forced_valid:
                return _filter_disallowed_models(task, forced_valid)
            if forced:
                logger.warning("model_keys_override: unknown model keys: %s", forced)

        forced_task_json = (os.getenv("LLM_FORCE_TASK_MODELS_JSON") or "").strip()
        if forced_task_json:
            try:
                mapping = json.loads(forced_task_json)
                if isinstance(mapping, dict) and task in mapping:
                    forced = _split_model_keys(mapping.get(task))
                    forced_valid = _normalize_forced_models(forced)
                    if forced_valid:
                        return _filter_disallowed_models(task, forced_valid)
                    if forced:
                        logger.warning("LLM_FORCE_TASK_MODELS_JSON: unknown model keys for task=%s: %s", task, forced)
            except Exception as e:
                logger.warning("LLM_FORCE_TASK_MODELS_JSON parse failed; ignoring override: %s", e)

        forced_all = (os.getenv("LLM_FORCE_MODELS") or os.getenv("LLM_FORCE_MODEL") or "").strip()
        if forced_all:
            forced = _split_model_keys(forced_all)
            forced_valid = _normalize_forced_models(forced)
            if forced_valid:
                return _filter_disallowed_models(task, forced_valid)
            if forced:
                logger.warning("LLM_FORCE_MODELS: unknown model keys: %s", forced)

        task_conf = self.config.get("tasks", {}).get(task, {})
        override_conf = self.task_overrides.get(task, {}) if hasattr(self, "task_overrides") else {}
        tier = override_conf.get("tier") or task_conf.get("tier") or "standard"
        
        tier_models = []
        # explicit models override wins
        if override_conf.get("models"):
            tier_models = override_conf["models"]
        else:
            # base tier models
            tier_models = self.config.get("tiers", {}).get(tier, [])
            # Allow tier override from llm_tier_candidates.yaml (opt-in)
            enable_candidates_override = os.getenv("LLM_ENABLE_TIER_CANDIDATES_OVERRIDE", "").lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
            config_dir = _BASE_CONFIG_PATH.parent
            local_candidates = config_dir / "llm_tier_candidates.local.yaml"
            candidates_path = local_candidates if local_candidates.exists() else (config_dir / "llm_tier_candidates.yaml")
            if enable_candidates_override and candidates_path.exists():
                try:
                    candidates = yaml.safe_load(candidates_path.read_text()).get("tiers", {})
                    if tier in candidates and candidates[tier]:
                        tier_models = candidates[tier]
                except Exception as e:
                    logger.warning(f"Failed to load tier candidates override: {e}")
        return _filter_disallowed_models(task, list(tier_models or []))

    def call(
        self,
        task: str,
        messages: List[Dict[str, str]],
        system_prompt_override: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[str] = None,
        model_keys: Optional[List[str]] = None,
        allow_fallback: Optional[bool] = None,
        **kwargs,
    ) -> Any:
        result = self._call_internal(
            task=task,
            messages=messages,
            system_prompt_override=system_prompt_override,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            return_raw=False,
            model_keys=model_keys,
            allow_fallback=allow_fallback,
            **kwargs,
        )
        _trace_llm_call(
            task=task,
            messages=messages,
            result=result,
            request_options={
                "system_prompt_override": system_prompt_override,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "response_format": response_format,
                "model_keys": model_keys,
                "allow_fallback": allow_fallback,
                **kwargs,
            },
        )
        return result["content"]

    def call_with_raw(
        self,
        task: str,
        messages: List[Dict[str, str]],
        system_prompt_override: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[str] = None,
        model_keys: Optional[List[str]] = None,
        allow_fallback: Optional[bool] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Like call(), but returns a dict with content, raw response, usage, request_id,
        model/provider, fallback chain, and latency_ms.
        """
        result = self._call_internal(
            task=task,
            messages=messages,
            system_prompt_override=system_prompt_override,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            return_raw=True,
            model_keys=model_keys,
            allow_fallback=allow_fallback,
            **kwargs,
        )
        _trace_llm_call(
            task=task,
            messages=messages,
            result=result,
            request_options={
                "system_prompt_override": system_prompt_override,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "response_format": response_format,
                "model_keys": model_keys,
                "allow_fallback": allow_fallback,
                **kwargs,
            },
        )
        return result

    def _call_internal(
        self,
        task: str,
        messages: List[Dict[str, str]],
        system_prompt_override: Optional[str],
        temperature: Optional[float],
        max_tokens: Optional[int],
        response_format: Optional[str],
        return_raw: bool,
        model_keys: Optional[List[str]],
        allow_fallback: Optional[bool],
        **kwargs,
    ) -> Dict[str, Any]:
        models = self.get_models_for_task(task, model_keys_override=model_keys)
        if not models:
            raise ValueError(f"No models available for task: {task}")

        last_error = None
        last_status = None
        last_error_class = None

        task_conf = self.config.get("tasks", {}).get(task, {})
        override_conf = self.task_overrides.get(task, {}) if hasattr(self, "task_overrides") else {}

        # -------------------------------------------------------------------
        # Strict model selection policy (NO silent downgrade)
        #
        # User requirement:
        # - If a human explicitly forces a model, we must NOT silently swap to a different model/provider,
        #   and we must NOT auto-failover to Codex/THINK. Instead, STOP and report.
        #
        # What counts as "explicit" (strict by default):
        # - call(..., model_keys=[...]) was provided
        # - env overrides are present: LLM_FORCE_MODELS / LLM_FORCE_MODEL / LLM_FORCE_TASK_MODELS_JSON
        # - per-task config sets allow_fallback: false
        #
        # Override:
        # - allow_fallback=True explicitly allows trying multiple models from the candidate list,
        #   but still does not allow Codex/THINK failover (models must remain within the explicit set).
        # -------------------------------------------------------------------
        forced_env_models = bool(
            (os.getenv("LLM_FORCE_TASK_MODELS_JSON") or "").strip()
            or (os.getenv("LLM_FORCE_MODELS") or os.getenv("LLM_FORCE_MODEL") or "").strip()
        )
        conf_allow_fallback = None
        if isinstance(override_conf, dict) and "allow_fallback" in override_conf:
            conf_allow_fallback = override_conf.get("allow_fallback")
        elif isinstance(task_conf, dict) and "allow_fallback" in task_conf:
            conf_allow_fallback = task_conf.get("allow_fallback")
        if allow_fallback is None and conf_allow_fallback is not None:
            allow_fallback = bool(conf_allow_fallback)

        strict_model_selection = bool(model_keys) or forced_env_models or (allow_fallback is False)
        allow_fallback_effective = allow_fallback if allow_fallback is not None else (not strict_model_selection)
        if strict_model_selection and not allow_fallback_effective and len(models) > 1:
            logger.warning("Router: strict model selection; fallback disabled (trying only %s)", models[0])

        # System Prompt Injection/Override logic (override > task_conf > existing)
        sp_override = system_prompt_override or override_conf.get("system_prompt_override") or task_conf.get("system_prompt_override")
        if sp_override:
            if messages and messages[0]["role"] == "system":
                messages[0]["content"] = sp_override
            else:
                messages.insert(0, {"role": "system", "content": sp_override})

        # Consolidate options for param guard
        #
        # Router config historically used a few shapes:
        # - tasks.<task>.options: {...}
        # - tasks.<task>.defaults: {...}
        # - tasks.<task>.timeout / max_tokens / response_format (flat keys)
        #
        # Keep backward compatibility by merging these into a single options dict.
        task_options: Dict[str, Any] = {}
        for key in ("options", "defaults"):
            v = task_conf.get(key)
            if isinstance(v, dict):
                task_options.update(v)
        for key in (
            "timeout",
            "max_tokens",
            "max_output_tokens",
            "max_completion_tokens",
            "response_format",
            "temperature",
            "top_p",
            "seed",
            "stop",
            "n",
            "aspect_ratio",
        ):
            if key in task_conf and task_conf.get(key) is not None:
                task_options.setdefault(key, task_conf.get(key))

        override_options: Dict[str, Any] = {}
        for key in ("options", "defaults"):
            v = override_conf.get(key)
            if isinstance(v, dict):
                override_options.update(v)
        for key in (
            "timeout",
            "max_tokens",
            "max_output_tokens",
            "max_completion_tokens",
            "response_format",
            "temperature",
            "top_p",
            "seed",
            "stop",
            "n",
            "aspect_ratio",
        ):
            if key in override_conf and override_conf.get(key) is not None:
                override_options.setdefault(key, override_conf.get(key))

        # Normalize max token keys to OpenAI-style "max_tokens" before sanitize_params.
        for opt in (task_options, override_options):
            if "max_tokens" not in opt and "max_output_tokens" in opt:
                opt["max_tokens"] = opt.pop("max_output_tokens")
            if "max_tokens" not in opt and "max_completion_tokens" in opt:
                opt["max_tokens"] = opt.pop("max_completion_tokens")
        base_options: Dict[str, Any] = {
            **task_options,
            **override_options,
            **kwargs,
        }
        # Only override defaults when explicitly provided.
        if temperature is not None:
            base_options["temperature"] = temperature
        if max_tokens is not None:
            base_options["max_tokens"] = max_tokens
        if response_format is not None:
            base_options["response_format"] = response_format
        # Internal-only hint: include the configured model chain in the app-level cache key.
        # Without this, changing model order/chain can keep returning stale cached results.
        try:
            if "_model_chain" not in base_options:
                base_options["_model_chain"] = list(models if allow_fallback_effective else models[:1])
        except Exception:
            pass

        # Task guardrails: keep "length-only shrink" responses bounded.
        #
        # Background:
        # - script_validation can optionally attempt an emergency shrink when the only hard failure is
        #   length_too_long (opt-in via SCRIPT_VALIDATION_AUTO_LENGTH_FIX=1).
        # - The caller may request very large max_tokens budgets, but for shrink we want strict brevity to
        #   actually land under target_chars_max (and avoid expensive retries / cache poisoning).
        if task == "script_a_text_quality_shrink":
            try:
                # Default: keep enough headroom to avoid finish_reason=length truncation while still
                # preventing runaway budgets on long scripts.
                cap = int(os.getenv("SCRIPT_VALIDATION_SHRINK_MAX_TOKENS", "12000"))
                if cap > 0:
                    mt = base_options.get("max_tokens")
                    if mt is None or int(mt) > cap:
                        base_options["max_tokens"] = cap
            except Exception:
                pass

        agent_result = maybe_handle_agent_mode(
            task=task,
            messages=messages,
            options=base_options,
            response_format=response_format,
            return_raw=return_raw,
        )
        if agent_result is not None:
            return agent_result

        # App-level API cache (cost-saving reruns).
        # - Only applies to text/chat tasks (image tasks are excluded by default).
        # - Cache key is computed from (task + messages + semantic options).
        if _api_cache_enabled_for_task(task):
            cached = _api_cache_read(task, messages, base_options)
            if isinstance(cached, dict):
                meta = cached.get("meta") or {}
                usage = cached.get("usage") or {}
                content = cached.get("content", "")
                task_id = cached.get("task_id") or _api_cache_task_id(task, messages, base_options)
                cache_file = _api_cache_path(str(task_id))
                chain = meta.get("chain") or ["cache"]
                model_key = meta.get("model_key") or meta.get("model") or "cache"
                provider_name = meta.get("provider") or "cache"
                req_id = meta.get("request_id") or str(task_id)
                finish_reason = meta.get("finish_reason")
                retry_meta = meta.get("retry")
                if isinstance(content, str) and not content.strip():
                    # Empty cached content is almost always a provider extraction glitch (or a bad run).
                    # Do not return it as a "successful" response; fall through to a real API call.
                    logger.info(f"Router: cache hit but empty content for {task} (task_id={task_id}); ignoring cache")
                else:
                    rf = base_options.get("response_format") or response_format
                    wants_json = rf == "json_object" or (
                        isinstance(rf, dict) and str(rf.get("type") or "").strip().lower() == "json_object"
                    )
                    if isinstance(content, str) and _FIREWORKS_FINAL_MARKER_RE.search(content):
                        logger.info(
                            f"Router: cache hit but contains final marker for {task} (task_id={task_id}); ignoring cache"
                        )
                    elif wants_json and not _is_parseable_json_value(str(content or "")):
                        logger.info(
                            f"Router: cache hit but invalid JSON for {task} (task_id={task_id}); ignoring cache"
                        )
                    else:
                        logger.info(f"Router: cache hit for {task} (task_id={task_id})")
                        routing_key = (os.getenv("LLM_ROUTING_KEY") or "").strip() or None
                        self._log_usage(
                            {
                                "status": "success",
                                "task": task,
                                "task_id": str(task_id),
                                "routing_key": routing_key,
                                "model": model_key,
                                "provider": provider_name,
                                "chain": chain,
                                "latency_ms": 0,
                                "usage": usage,
                                "request_id": req_id,
                                "finish_reason": finish_reason,
                                "retry": retry_meta,
                                "cache": {"hit": True, "path": str(cache_file)},
                                "timestamp": time.time(),
                            }
                        )
                        return {
                            "content": content,
                            "raw": None,
                            "usage": usage,
                            "request_id": req_id,
                            "model": model_key,
                            "provider": provider_name,
                            "chain": chain,
                            "latency_ms": 0,
                            "finish_reason": finish_reason,
                            "retry": retry_meta,
                            "cache": {"hit": True, "path": str(cache_file), "task_id": str(task_id)},
                        }

        # Optional: Codex exec (subscription) first layer with API fallback.
        # - Runs `codex exec --sandbox read-only` to avoid writing to the repo/workspaces.
        # - If Codex is unavailable or output is invalid, fall back to the existing API router path.
        routing: Dict[str, Any] | None = None
        if not strict_model_selection:
            codex_content, codex_meta = try_codex_exec(
                task=task,
                messages=messages,  # type: ignore[arg-type]
                response_format=str(base_options.get("response_format") or response_format or "").strip() or None,
            )
            if codex_meta.get("attempted") and codex_content is not None:
                latency_ms = int((codex_meta or {}).get("latency_ms") or 0)
                task_id = None
                try:
                    task_id = _api_cache_task_id(task, messages, base_options)
                except Exception:
                    task_id = None
                req_id = f"codex_exec:{task_id}" if task_id else "codex_exec"
                model_label = str((codex_meta or {}).get("model") or "default")
                chain = ["codex_exec"]
                self._log_usage(
                    {
                        "status": "success",
                        "task": task,
                        "task_id": str(task_id) if task_id else None,
                        "routing_key": (os.getenv("LLM_ROUTING_KEY") or "").strip() or None,
                        "model": f"codex:{model_label}",
                        "provider": "codex_exec",
                        "chain": chain,
                        "latency_ms": latency_ms,
                        "usage": {},
                        "request_id": req_id,
                        "finish_reason": "stop",
                        "retry": None,
                        "routing": {"codex_exec": codex_meta},
                        "timestamp": time.time(),
                    }
                )
                return {
                    "content": codex_content,
                    "raw": {"provider": "codex_exec", "meta": codex_meta} if return_raw else None,
                    "usage": {},
                    "request_id": req_id,
                    "model": f"codex:{model_label}",
                    "provider": "codex_exec",
                    "chain": chain,
                    "latency_ms": latency_ms,
                    "finish_reason": "stop",
                    "retry": None,
                }
            if codex_meta.get("attempted") and codex_content is None:
                routing = routing or {}
                routing["codex_exec"] = codex_meta

        # Optional: split traffic between Azure and non-Azure providers (roughly).
        # - Enable via env: LLM_AZURE_SPLIT_RATIO=0.5
        # - Use a stable routing key when provided (env: LLM_ROUTING_KEY); otherwise fall back to task_id hash.
        if not strict_model_selection:
            ratio = _parse_ratio_env("LLM_AZURE_SPLIT_RATIO")
            if ratio is not None:
                azure_models: List[str] = []
                other_models: List[str] = []
                for mk in models:
                    conf = (self.config.get("models", {}) or {}).get(mk) or {}
                    provider = conf.get("provider")
                    if provider == "azure":
                        azure_models.append(mk)
                    else:
                        other_models.append(mk)
                if azure_models and other_models:
                    route_key = (os.getenv("LLM_ROUTING_KEY") or "").strip()
                    if not route_key:
                        try:
                            route_key = _api_cache_task_id(task, messages, base_options)
                        except Exception:
                            route_key = f"{task}:{len(messages)}"
                    bucket = _split_bucket(route_key)
                    prefer_azure = bucket < float(ratio)
                    models = (azure_models + other_models) if prefer_azure else (other_models + azure_models)
                    routing = routing or {}
                    routing.update(
                        {
                            "policy": "azure_split_ratio",
                            "ratio": float(ratio),
                            "bucket": bucket,
                            "preferred_provider": "azure" if prefer_azure else "non_azure",
                            "routing_key": route_key,
                        }
                    )

        if not allow_fallback_effective and models:
            models = models[:1]

        tried = []
        total_wait = 0.0
        status_counts = {}
        for model_key in models:
            model_conf = self.config.get("models", {}).get(model_key)
            if not model_conf:
                continue

            provider_name = model_conf.get("provider")
            client = self.clients.get(provider_name)

            if not client:
                logger.debug(f"Client for {provider_name} not ready. Skipping {model_key}")
                continue

            try:
                safe_options = sanitize_params(model_conf, base_options)
                # Clamp completion budgets to the model capability caps.
                # Some task overrides intentionally request very large max_tokens, but providers/models can
                # impose hard caps. Keeping `safe_options` within caps avoids misleading "cannot increase"
                # retry errors when finish_reason == "length".
                caps = (model_conf.get("capabilities", {}) or {})
                for k in ("max_tokens", "max_completion_tokens"):
                    if k not in safe_options:
                        continue
                    cap_v = caps.get(k)
                    if cap_v in (None, ""):
                        continue
                    try:
                        cap_i = int(cap_v)
                        cur_i = int(safe_options[k])
                    except Exception:
                        continue
                    if cap_i > 0 and cur_i > cap_i:
                        safe_options[k] = cap_i
                logger.info(f"Router: Invoking {model_key} for {task}...")
                start = time.time()
                invoke_options = dict(safe_options)
                raw_result = self._invoke_provider(
                    provider_name,
                    client,
                    model_conf,
                    messages,
                    return_raw=True,
                    **safe_options,
                )
                finish_reason = _extract_finish_reason(raw_result)

                # Retry-on-truncation (finish_reason == "length") to keep low default caps safe.
                # This is cheaper than always setting very high max_tokens, while staying robust.
                retry_meta: Dict[str, Any] | None = None
                retry_enabled = (os.getenv("LLM_RETRY_ON_LENGTH") or "1").strip().lower() not in {"0", "false", "no", "off"}
                if retry_enabled and finish_reason == "length":
                    max_key = None
                    cur_max = None
                    for k in ("max_tokens", "max_completion_tokens"):
                        v = safe_options.get(k)
                        if isinstance(v, int) and v > 0:
                            max_key = k
                            cur_max = v
                            break
                    caps = model_conf.get("capabilities", {}) or {}
                    cap = None
                    if max_key == "max_completion_tokens":
                        cap = caps.get("max_completion_tokens")
                    elif max_key == "max_tokens":
                        cap = caps.get("max_tokens")
                    try:
                        cap = int(cap) if cap is not None else None
                    except Exception:
                        cap = None

                    mult_raw = (os.getenv("LLM_LENGTH_RETRY_MULTIPLIER") or "2").strip()
                    try:
                        mult = max(1.0, float(mult_raw))
                    except Exception:
                        mult = 2.0
                    env_cap_raw = (os.getenv("LLM_LENGTH_RETRY_MAX_TOKENS") or "").strip()
                    env_cap = None
                    if env_cap_raw:
                        try:
                            env_cap = int(env_cap_raw)
                        except Exception:
                            env_cap = None
                    hard_cap = cap if cap is not None else env_cap
                    if hard_cap is not None and env_cap is not None:
                        hard_cap = min(hard_cap, env_cap)

                    if max_key and cur_max:
                        new_max = int(max(cur_max + 1, round(cur_max * mult)))
                        if hard_cap is not None:
                            new_max = min(new_max, hard_cap)
                        if new_max <= cur_max:
                            raise RuntimeError(
                                f"finish_reason=length but cannot increase {max_key} "
                                f"(current={cur_max}, cap={hard_cap})"
                            )
                        logger.warning(
                            "Router: %s returned finish_reason=length; retrying with %s=%s (was %s)",
                            model_key,
                            max_key,
                            new_max,
                            cur_max,
                        )
                        # Propagate the higher cap for subsequent candidates in this call.
                        base_options["max_tokens"] = new_max
                        retry_opts = dict(safe_options)
                        retry_opts[max_key] = new_max
                        raw_retry = self._invoke_provider(
                            provider_name,
                            client,
                            model_conf,
                            messages,
                            return_raw=True,
                            **retry_opts,
                        )
                        finish_reason_retry = _extract_finish_reason(raw_retry)
                        retry_meta = {
                            "reason": "finish_reason_length",
                            "max_key": max_key,
                            "from": cur_max,
                            "to": new_max,
                            "finish_reason": finish_reason_retry,
                        }
                        if finish_reason_retry == "length":
                            raise RuntimeError(
                                f"finish_reason=length after retry (max={new_max}); try next model"
                            )
                        raw_result = raw_retry
                        finish_reason = finish_reason_retry
                        invoke_options = retry_opts

                content = self._extract_content(provider_name, model_conf, raw_result)
                if isinstance(content, str) and not content.strip():
                    # Fireworks occasionally returns an empty/marker-only completion (200 OK but no usable text).
                    # Retry the same model a few times before falling back to the next provider (OpenRouter may be
                    # unavailable/out-of-credits, and we want to avoid THINK-mode queue escalation).
                    if provider_name == "fireworks":
                        try:
                            empty_retry = int((os.getenv("LLM_FIREWORKS_EMPTY_RETRY") or "2").strip())
                        except Exception:
                            empty_retry = 2
                        empty_retry = max(0, min(empty_retry, 5))
                        for attempt in range(empty_retry):
                            logger.warning(
                                "Router: %s returned empty content; retrying (fireworks) (%s/%s)",
                                model_key,
                                attempt + 1,
                                empty_retry,
                            )
                            raw_retry2 = self._invoke_provider(
                                provider_name,
                                client,
                                model_conf,
                                messages,
                                return_raw=True,
                                **invoke_options,
                            )
                            finish_reason2 = _extract_finish_reason(raw_retry2)
                            content2 = self._extract_content(provider_name, model_conf, raw_retry2)
                            if isinstance(content2, str) and content2.strip():
                                raw_result = raw_retry2
                                finish_reason = finish_reason2
                                content = content2
                                if retry_meta is None:
                                    retry_meta = {
                                        "reason": "empty_content",
                                        "attempts": attempt + 1,
                                        "finish_reason": finish_reason2,
                                    }
                                else:
                                    retry_meta = dict(retry_meta)
                                    retry_meta["empty_content_retry"] = {
                                        "attempts": attempt + 1,
                                        "finish_reason": finish_reason2,
                                    }
                                break
                    raise RuntimeError("empty_content")
                usage = self._extract_usage(raw_result)
                req_id = _extract_request_id(raw_result)
                latency_ms = int((time.time() - start) * 1000)
                chain = tried + [model_key]
                task_id = None
                try:
                    task_id = _api_cache_task_id(task, messages, base_options)
                except Exception:
                    task_id = None
                cache_write_path = None
                if finish_reason != "length":
                    cache_write_path = _api_cache_write(
                        task,
                        messages,
                        base_options,
                        payload={
                            "content": content,
                            "usage": usage,
                            "meta": {
                                "provider": provider_name,
                                "model_key": model_key,
                                "chain": chain,
                                "request_id": req_id,
                                "finish_reason": finish_reason,
                                "retry": retry_meta,
                            },
                        },
                    )
                logger.info(
                    f"Router: {task} succeeded via {model_key} "
                    f"(fallback_chain={chain}, latency_ms={latency_ms}, usage={usage}, request_id={req_id})"
                )
                log_payload = {
                    "status": "success",
                    "task": task,
                    "task_id": str(task_id) if task_id else None,
                    "routing_key": (os.getenv("LLM_ROUTING_KEY") or "").strip() or None,
                    "model": model_key,
                    "provider": provider_name,
                    "chain": chain,
                    "latency_ms": latency_ms,
                    "usage": usage,
                    "request_id": req_id,
                    "finish_reason": finish_reason,
                    "timestamp": time.time(),
                }
                if routing:
                    log_payload["routing"] = routing
                if retry_meta:
                    log_payload["retry"] = retry_meta
                if cache_write_path:
                    log_payload["cache"] = {"write": True, "path": str(cache_write_path)}
                # Drop nulls to keep the JSONL tidy
                log_payload = {k: v for k, v in log_payload.items() if v is not None}
                self._log_usage(log_payload)
                return {
                    "content": content,
                    "raw": raw_result if return_raw else None,
                    "usage": usage,
                    "request_id": req_id,
                    "model": model_key,
                    "provider": provider_name,
                    "chain": chain,
                    "latency_ms": latency_ms,
                    "finish_reason": finish_reason,
                    "retry": retry_meta,
                    "cache": {"write": True, "path": str(cache_write_path)} if cache_write_path else None,
                    "routing": routing,
                }
            except Exception as e:
                status = _extract_status(e)
                logger.warning(f"Failed to call {model_key}: {e} (status={status})")
                last_error = e
                last_status = status
                last_error_class = e.__class__.__name__
                tried.append(model_key)
                # Always try the next candidate model on failure.
                # Apply backoff only for transient-ish statuses (429/5xx/etc).
                transient_statuses = set(self.fallback_policy.get("transient_statuses", [])) or TRANSIENT_STATUSES
                backoff_sec = float(self.fallback_policy.get("backoff_sec", 1.0))
                retry_limit = int(self.fallback_policy.get("retry_limit", 0))
                max_attempts = int(self.fallback_policy.get("max_total_attempts", 0))
                max_wait = float(self.fallback_policy.get("max_total_wait_sec", 0))
                per_status_retry = self.fallback_policy.get("per_status_retry", {}) or {}

                if retry_limit and len(tried) >= retry_limit:
                    break

                if max_attempts and len(tried) >= max_attempts:
                    break

                if status not in transient_statuses | {None}:
                    continue
                if status in transient_statuses:
                    if status is not None:
                        status_counts[status] = status_counts.get(status, 0) + 1
                        limit = int(per_status_retry.get(str(status), 0))
                        if limit and status_counts[status] >= limit:
                            break
                    per_status = self.fallback_policy.get("per_status_backoff", {}) or {}
                    sleep_for = float(per_status.get(str(status), backoff_sec))
                    if max_wait and (total_wait + sleep_for) > max_wait:
                        break
                    time.sleep(sleep_for)  # short backoff to avoid hammering provider
                    total_wait += sleep_for
                continue

        self._log_usage(
            {
                "status": "fail",
                "task": task,
                "routing_key": (os.getenv("LLM_ROUTING_KEY") or "").strip() or None,
                "chain": tried,
                "strict_model_selection": strict_model_selection,
                "allow_fallback": allow_fallback_effective,
                "error": str(last_error),
                "error_class": last_error_class,
                "status_code": last_status,
                "timestamp": time.time(),
            }
        )
        if not strict_model_selection:
            failover = maybe_failover_to_think(
                task=task,
                messages=messages,
                options=base_options,
                response_format=response_format,
                return_raw=return_raw,
                failure={
                    "error": str(last_error) if last_error is not None else None,
                    "error_class": last_error_class,
                    "status_code": last_status,
                    "chain": tried,
                },
            )
            if failover is not None:
                return failover

        raise RuntimeError(f"All models failed for task '{task}'. tried={tried} last_error={last_error}")

    def _invoke_provider(self, provider, client, model_conf, messages, return_raw: bool = False, **kwargs):
        cap = model_conf.get("capabilities", {})
        mode = cap.get("mode", "chat")
        
        # Merge defaults
        defaults = model_conf.get("defaults", {})
        params = {**defaults, **kwargs}

        # Fireworks: requests with max_tokens > 4096 require streaming mode.
        fireworks_needs_stream = False
        if provider == "fireworks":
            for k in ("max_tokens", "max_completion_tokens"):
                v = params.get(k)
                if v is None:
                    continue
                try:
                    if int(v) > 4096:
                        fireworks_needs_stream = True
                        break
                except Exception:
                    continue

        # Fireworks parity with OpenRouter:
        # - DeepSeek V3.2 exp must run with thinking enabled by default (callers can still override explicitly).
        if provider == "fireworks":
            try:
                mn = str(model_conf.get("model_name") or "").strip().lower()
            except Exception:
                mn = ""
            if "deepseek-v3.2-exp" in mn:
                eb = params.get("extra_body")
                if not isinstance(eb, dict):
                    eb = {}
                reasoning = eb.get("reasoning")
                if not isinstance(reasoning, dict):
                    eb["reasoning"] = {"enabled": True, "exclude": True}
                else:
                    # Thinking is mandatory: never allow callers to silently disable it for this model.
                    reasoning["enabled"] = True
                    if "exclude" not in reasoning:
                        reasoning["exclude"] = True
                    eb["reasoning"] = reasoning
                params["extra_body"] = eb

        fireworks_reasoning_enabled = False
        fireworks_reasoning_exclude = False
        fireworks_json_mode = False
        if provider == "fireworks":
            eb0 = params.get("extra_body")
            if isinstance(eb0, dict):
                reasoning0 = eb0.get("reasoning")
                if isinstance(reasoning0, dict):
                    fireworks_reasoning_enabled = reasoning0.get("enabled") is True
                    fireworks_reasoning_exclude = reasoning0.get("exclude") is True
            rf0 = params.get("response_format")
            fireworks_json_mode = rf0 == "json_object" or (
                isinstance(rf0, dict) and str(rf0.get("type") or "").strip().lower() == "json_object"
            )
            if fireworks_reasoning_enabled and fireworks_reasoning_exclude:
                # Ensure enough completion budget so the model can think and still output the final answer.
                for k in ("max_tokens", "max_completion_tokens"):
                    if k in params:
                        try:
                            params[k] = max(int(params[k]), _FIREWORKS_REASONING_MIN_MAX_TOKENS)
                        except Exception:
                            pass

        # OpenRouter quirk/guard:
        # - `moonshotai/kimi-k2-thinking` can return empty content unless `extra_body.reasoning.enabled=true` is set.
        # - For script-writing we also require thinking for `deepseek/deepseek-v3.2-exp`.
        # - For safety, enable reasoning by default for these models when callers forgot to attach it.
        #   (Callers can still override by explicitly providing extra_body.reasoning.)
        if provider == "openrouter":
            try:
                mn = str(model_conf.get("model_name") or "").strip().lower()
            except Exception:
                mn = ""
            if ("kimi-k2-thinking" in mn) or ("deepseek-v3.2-exp" in mn):
                eb = params.get("extra_body")
                if not isinstance(eb, dict):
                    eb = {}
                reasoning = eb.get("reasoning")
                if not isinstance(reasoning, dict):
                    eb["reasoning"] = {"enabled": True, "exclude": True}
                else:
                    # Thinking is mandatory: never allow callers to silently disable it for these models.
                    reasoning["enabled"] = True
                    if "exclude" not in reasoning:
                        reasoning["exclude"] = True
                    eb["reasoning"] = reasoning
                params["extra_body"] = eb

        # Fireworks quirks/compat:
        # - Fireworks is OpenAI-compatible, but supports extra params via request JSON (sent as extra_body via SDK).
        # - Existing task overrides in this repo may use OpenRouter-style `extra_body.reasoning.enabled`.
        #   When provider=fireworks, translate that into Fireworks' `reasoning_effort` and drop `reasoning`.
        if provider == "fireworks":
            fireworks_extra_keys = {
                "reasoning_effort",
                "prompt_cache_isolation_key",
                "context_length_exceeded_behavior",
                "ignore_eos",
                "perf_metrics_in_response",
                "top_k",
                "mirostat_target",
                "mirostat_lr",
            }
            fw_extra: Dict[str, Any] = {}
            for key in fireworks_extra_keys:
                if key in params:
                    fw_extra[key] = params.pop(key)
            if fw_extra:
                eb = params.get("extra_body")
                if not isinstance(eb, dict):
                    eb = {}
                eb = {**eb, **{k: v for k, v in fw_extra.items() if v is not None}}
                params["extra_body"] = eb

            eb = params.get("extra_body")
            if isinstance(eb, dict) and "reasoning" in eb:
                reasoning = eb.get("reasoning")
                enabled = None
                exclude = False
                if isinstance(reasoning, dict):
                    enabled = reasoning.get("enabled")
                    exclude = reasoning.get("exclude") is True
                eb = dict(eb)
                # NOTE: `exclude: true` means "hide reasoning from output" (OpenRouter style), not "disable thinking".
                if (enabled is True or (exclude and enabled is not False)) and "reasoning_effort" not in eb:
                    eb["reasoning_effort"] = "high"
                eb.pop("reasoning", None)
                params["extra_body"] = eb

            if (
                fireworks_reasoning_enabled
                and fireworks_reasoning_exclude
                and not fireworks_json_mode
            ):
                marker = _FIREWORKS_FINAL_MARKER
                marker_msg = (
                    "最終出力は必ず次のマーカー行の後ろに書いてください。\n"
                    f"{marker}\n"
                    "マーカー行より前は無視します。マーカー行より後ろは最終出力だけを書き、余計な文章を書かないでください。"
                )
                messages = [*list(messages), {"role": "system", "content": marker_msg}]
        
        # Params are already sanitized in call(); just forward with minimal mapping
        api_args = {}
        for k, v in params.items():
            if v is None:
                continue
            if k == "extra_body":
                # OpenRouter supports provider-specific extensions (e.g. reasoning.enabled).
                # Other providers (Azure, etc.) should never receive extra_body.
                if provider not in {"openrouter", "fireworks"}:
                    continue
                if not isinstance(v, dict):
                    continue
                extra_body = dict(v)
                if provider == "openrouter":
                    reasoning = extra_body.get("reasoning")
                    if reasoning is not None and not _openrouter_model_allows_reasoning(model_conf.get("model_name")):
                        # Only allow reasoning payload for explicitly allowlisted OpenRouter models.
                        extra_body.pop("reasoning", None)
                if provider == "fireworks":
                    reasoning = extra_body.pop("reasoning", None)
                    if (
                        isinstance(reasoning, dict)
                        and (reasoning.get("enabled") is True or (reasoning.get("exclude") is True and reasoning.get("enabled") is not False))
                        and "reasoning_effort" not in extra_body
                    ):
                        extra_body["reasoning_effort"] = "high"
                if extra_body:
                    api_args["extra_body"] = extra_body
                continue
            if k == "response_format" and v == "json_object":
                if cap.get("json_mode"):
                    api_args["response_format"] = {"type": "json_object"}
                else:
                    # モデルが JSON mode 未対応ならそのままプロンプトに任せる
                    pass
                continue
            api_args[k] = v

        # IMAGE GENERATION
        if mode == "image_generation":
            return self._invoke_image_gen(provider, client, model_conf, messages, **kwargs)

        # TEXT/CHAT
        model_name = model_conf.get("deployment") if provider == "azure" else model_conf.get("model_name")
        if provider == "fireworks":
            model_name = _fireworks_model_id_from_logical(str(model_name or ""))
        
        if provider == "azure":
             # Azure specific
             pass
        
        # Common OpenAI/Azure Interface
        if provider in ["azure", "openrouter", "fireworks"]:
            # Fireworks: requests with max_tokens > 4096 require streaming mode.
            if provider == "fireworks":
                try:
                    for k in ("max_tokens", "max_completion_tokens"):
                        v = api_args.get(k)
                        if v is None:
                            continue
                        if int(v) > 4096:
                            api_args.setdefault("stream", True)
                            break
                except Exception:
                    pass

            if provider == "fireworks" and api_args.get("stream") is True:
                from types import SimpleNamespace

                fw_client = client
                last_exc: Optional[Exception] = None
                attempts = max(1, int(len(getattr(self, "_fireworks_keys", []) or [])))
                for _ in range(attempts):
                    try:
                        stream = fw_client.chat.completions.create(model=model_name, messages=messages, **api_args)
                        client = fw_client
                        break
                    except Exception as exc:
                        last_exc = exc
                        status = _extract_status(exc)
                        if provider == "fireworks" and status in {401, 402, 403, 404, 412}:
                            self._fireworks_mark_current_key_dead(http_status=status)
                            nxt = self._fireworks_rotate_client()
                            if nxt is not None:
                                fw_client = nxt
                                continue
                        raise
                else:
                    if last_exc is not None:
                        raise last_exc
                    raise RuntimeError("Fireworks streaming request failed")
                parts: List[str] = []
                finish_reason = None
                last_id = None
                last_model = None
                usage_obj = None
                final_message_content = None
                for chunk in stream:
                    if isinstance(chunk, dict):
                        last_id = chunk.get("id", last_id)
                        last_model = chunk.get("model", last_model)
                        choices = chunk.get("choices")
                        u = chunk.get("usage")
                    else:
                        last_id = getattr(chunk, "id", last_id)
                        last_model = getattr(chunk, "model", last_model)
                        choices = getattr(chunk, "choices", None)
                        u = getattr(chunk, "usage", None)

                    if choices:
                        c0 = choices[0]
                        if isinstance(c0, dict):
                            delta = c0.get("delta")
                            fr = c0.get("finish_reason")
                        else:
                            delta = getattr(c0, "delta", None)
                            fr = getattr(c0, "finish_reason", None)

                        piece = None
                        if isinstance(delta, dict):
                            piece = delta.get("content")
                        elif delta is not None:
                            piece = getattr(delta, "content", None)
                        if piece:
                            parts.append(str(piece))
                        else:
                            # Some providers include full content in `message` on the final chunk instead of `delta`.
                            if isinstance(c0, dict):
                                msg = c0.get("message")
                                if isinstance(msg, dict) and msg.get("content"):
                                    final_message_content = msg.get("content")
                                elif c0.get("text"):
                                    final_message_content = c0.get("text")
                            else:
                                msg = getattr(c0, "message", None)
                                if msg is not None:
                                    mc = getattr(msg, "content", None)
                                    if mc:
                                        final_message_content = mc
                                else:
                                    tc = getattr(c0, "text", None)
                                    if tc:
                                        final_message_content = tc
                        if fr:
                            finish_reason = fr

                    if u is not None:
                        usage_obj = u
                content_text = "".join(parts)
                if (not content_text) and final_message_content:
                    content_text = str(final_message_content)
                try:
                    def _usage_get(obj, key: str):
                        if obj is None:
                            return None
                        if isinstance(obj, dict):
                            return obj.get(key)
                        return getattr(obj, key, None)

                    usage = (
                        SimpleNamespace(
                            prompt_tokens=_usage_get(usage_obj, "prompt_tokens"),
                            completion_tokens=_usage_get(usage_obj, "completion_tokens"),
                            total_tokens=_usage_get(usage_obj, "total_tokens"),
                        )
                        if usage_obj is not None
                        else None
                    )
                except Exception:
                    usage = None
                msg = SimpleNamespace(content=content_text)
                choice = SimpleNamespace(message=msg, finish_reason=finish_reason)
                response = SimpleNamespace(id=last_id, model=last_model, choices=[choice], usage=usage)
            else:
                if provider == "fireworks":
                    fw_client = client
                    last_exc: Optional[Exception] = None
                    attempts = max(1, int(len(getattr(self, "_fireworks_keys", []) or [])))
                    for _ in range(attempts):
                        try:
                            response = fw_client.chat.completions.create(
                                model=model_name, messages=messages, **api_args
                            )
                            client = fw_client
                            break
                        except Exception as exc:
                            last_exc = exc
                            status = _extract_status(exc)
                            if status in {401, 402, 403, 404, 412}:
                                self._fireworks_mark_current_key_dead(http_status=status)
                                nxt = self._fireworks_rotate_client()
                                if nxt is not None:
                                    fw_client = nxt
                                    continue
                            raise
                    else:
                        if last_exc is not None:
                            raise last_exc
                        raise RuntimeError("Fireworks request failed")
                else:
                    response = client.chat.completions.create(model=model_name, messages=messages, **api_args)
            if return_raw:
                return response
            content = response.choices[0].message.content
            if (
                provider == "fireworks"
                and fireworks_reasoning_enabled
                and fireworks_reasoning_exclude
            ):
                if fireworks_json_mode:
                    chunk = _extract_json_object_chunk(content or "")
                    if chunk is not None:
                        return chunk
                else:
                    return _extract_after_fireworks_marker(content or "")
            return content

        # Gemini Chat (Not implemented fully in config yet for Text, but ready)
        if provider == "gemini":
            # Convert messages to Gemini format
            # This is complex, skipping for now as we don't use Gemini for text in this config
            raise NotImplementedError("Gemini Text not supported yet")

    @staticmethod
    def _extract_content(provider: str, model_conf: Dict[str, Any], raw_result: Any) -> Any:
        cap = model_conf.get("capabilities", {}) or {}
        mode = cap.get("mode", "chat")
        if mode == "image_generation":
            return raw_result
        if provider in ["azure", "openrouter", "fireworks"]:
            try:
                content = raw_result.choices[0].message.content  # type: ignore[attr-defined]
                if provider == "fireworks" and isinstance(content, str) and "YTM_FINAL" in content:
                    return _extract_after_fireworks_marker(content)
                return content
            except Exception:
                pass
        return raw_result

    def _invoke_image_gen(self, provider, client, model_conf, messages, **kwargs):
        if provider == "gemini":
            # Extract prompt from messages
            # Usually the last user message
            prompt = ""
            for m in reversed(messages):
                if m["role"] == "user":
                    prompt = m["content"]
                    break

            if not prompt:
                raise ValueError("No prompt found for image generation")

            model_name = model_conf.get("model_name")

            # Use the configured genai client
            model = genai.GenerativeModel(model_name)

            # For image generation, just pass the prompt
            # Additional parameters will be handled by the model configuration
            try:
                response = model.generate_content(prompt)
                return response
            except Exception as e:
                logger.error(f"Error in Gemini image generation: {e}")
                raise
        else:
            raise NotImplementedError(f"Image generation not supported for provider: {provider}")

def get_router():
    return LLMRouter()

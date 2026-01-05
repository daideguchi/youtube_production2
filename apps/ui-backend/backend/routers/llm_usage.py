from __future__ import annotations

from typing import List, Dict, Any, Iterable, Tuple, Optional
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query
import json
import os
import hashlib
import time
import yaml

from factory_common import fireworks_keys as fw_keys
from factory_common.paths import logs_root, repo_root

LOG_PATH = logs_root() / "llm_usage.jsonl"
OVERRIDE_BASE_PATH = repo_root() / "configs" / "llm_task_overrides.yaml"
OVERRIDE_LOCAL_PATH = repo_root() / "configs" / "llm_task_overrides.local.yaml"
LLM_ROUTER_CONFIG_PATH = repo_root() / "configs" / "llm_router.yaml"
LLM_MODEL_CODES_PATH = repo_root() / "configs" / "llm_model_codes.yaml"
SCRIPTS_ROOT = repo_root() / "workspaces" / "scripts"

router = APIRouter(prefix="/api/llm-usage", tags=["llm_usage"])

def _deep_merge_dict(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = dict(base or {})
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge_dict(out.get(k) or {}, v)  # type: ignore[arg-type]
        else:
            out[k] = v
    return out


def _load_records(limit: int) -> List[Dict[str, Any]]:
    if not LOG_PATH.exists():
        return []
    records = []
    for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except Exception:
            continue
    return records[-limit:] if limit else records


@router.get("/")
def list_usage(limit: int = Query(200, ge=1, le=2000)):
    """
    Return recent LLM router usage logs (JSONL entries).
    """
    records = _load_records(limit)
    return {"count": len(records), "records": records}


def _iter_records() -> Iterable[Dict[str, Any]]:
    if not LOG_PATH.exists():
        return []
    def _gen():
        with LOG_PATH.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = (line or "").strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    continue
    return _gen()


def _sha256_hex(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _parse_fw_keys_from_text(text: str) -> List[str]:
    import re

    key_re = re.compile(r"^fw_[A-Za-z0-9_-]{10,}$")
    out: List[str] = []
    seen = set()
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
        if not key_re.match(line):
            continue
        if line in seen:
            continue
        out.append(line)
        seen.add(line)
    return out


def _read_fw_state_file(path) -> Dict[str, Dict[str, Any]]:
    try:
        p = path
        if hasattr(path, "exists") and not path.exists():
            return {}
        obj = json.loads(p.read_text(encoding="utf-8", errors="replace") or "null")
    except Exception:
        return {}
    if not isinstance(obj, dict):
        return {}
    keys_obj = obj.get("keys")
    return keys_obj if isinstance(keys_obj, dict) else {}


def _write_fw_state_file(path, *, keys_obj: Dict[str, Dict[str, Any]]) -> None:
    from datetime import datetime, timezone

    p = path
    payload = {
        "version": 1,
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "keys": keys_obj,
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except Exception:
        pass
    tmp.replace(p)
    try:
        os.chmod(p, 0o600)
    except Exception:
        pass


def _epoch_to_iso(ts: Any) -> Optional[str]:
    if ts is None:
        return None
    try:
        v = float(ts)
    except Exception:
        return None
    try:
        return datetime.fromtimestamp(v, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except Exception:
        return None


def _fireworks_pool_config(pool: str) -> Dict[str, str]:
    p = str(pool or "").strip().lower()
    if p == "script":
        return {
            "pool": "script",
            "primary_env": "FIREWORKS_SCRIPT",
            "primary_alias_env": "FIREWORKS_SCRIPT_API_KEY",
            "keys_inline_env": "FIREWORKS_SCRIPT_KEYS",
        }
    if p == "image":
        return {
            "pool": "image",
            "primary_env": "FIREWORKS_IMAGE",
            "primary_alias_env": "FIREWORKS_IMAGE_API_KEY",
            "keys_inline_env": "FIREWORKS_IMAGE_KEYS",
        }
    raise HTTPException(status_code=400, detail=f"invalid pool: {pool!r} (expected: script|image|all)")


def _get_fireworks_pool_status(pool: str) -> Dict[str, Any]:
    conf = _fireworks_pool_config(pool)
    keys = fw_keys.candidate_keys(pool)

    state_path = fw_keys.state_path(pool)
    keyring_path = fw_keys.keyring_path(pool)
    state = _read_fw_state_file(state_path)

    file_keys: List[str] = []
    try:
        if keyring_path.exists():
            file_keys = _parse_fw_keys_from_text(keyring_path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        file_keys = []

    env_primary = (os.getenv(conf["primary_env"]) or os.getenv(conf["primary_alias_env"]) or "").strip()
    env_inline = (os.getenv(conf["keys_inline_env"]) or "").strip()
    inline_keys = _parse_fw_keys_from_text(env_inline.replace(",", "\n")) if env_inline else []

    # Leases (map by key_fp)
    now = time.time()
    leases = fw_keys.list_active_leases()
    leases_by_fp: Dict[str, Dict[str, Any]] = {}
    for ent in leases:
        if not isinstance(ent, dict):
            continue
        fp = str(ent.get("key_fp") or "").strip()
        if not fp:
            continue
        leases_by_fp[fp] = ent

    rows: List[Dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for idx, k in enumerate(keys, start=1):
        fp = _sha256_hex(k)
        ent = state.get(fp) if isinstance(state.get(fp), dict) else {}
        st = str((ent or {}).get("status") or "unknown")
        counts[st] += 1
        lease = leases_by_fp.get(fp)

        source = "file"
        if env_primary and k == env_primary:
            source = "env"
        elif k in inline_keys and k not in file_keys:
            source = "inline"
        elif k not in file_keys:
            source = "env_or_inline"

        rows.append(
            {
                "index": idx,
                "masked": fw_keys.mask_key(k),
                "key_fp": fp,
                "source": source,
                "status": st,
                "last_checked_at": (ent or {}).get("last_checked_at"),
                "last_http_status": (ent or {}).get("last_http_status"),
                "ratelimit": (ent or {}).get("ratelimit"),
                "lease": (
                    {
                        "lease_id": str((lease or {}).get("lease_id") or "")[:8] if lease else None,
                        "agent": (lease or {}).get("agent") if lease else None,
                        "pid": (lease or {}).get("pid") if lease else None,
                        "host": (lease or {}).get("host") if lease else None,
                        "purpose": (lease or {}).get("purpose") if lease else None,
                        "expires_at": _epoch_to_iso((lease or {}).get("expires_at")) if lease else None,
                        "expires_in_sec": (
                            max(0, int(float((lease or {}).get("expires_at") or 0) - now)) if lease else None
                        ),
                    }
                    if lease
                    else None
                ),
            }
        )

    return {
        "pool": conf["pool"],
        "keyring_path": str(keyring_path),
        "state_path": str(state_path),
        "keys": rows,
        "counts": [{"status": k, "count": int(v)} for k, v in sorted(counts.items())],
    }


def _probe_fireworks_pool(pool: str, *, limit: int = 0) -> Dict[str, Any]:
    pool = str(pool or "").strip().lower()
    keys = fw_keys.candidate_keys(pool)
    if limit and limit > 0:
        keys = keys[: int(limit)]

    state_path = fw_keys.state_path(pool)
    state = _read_fw_state_file(state_path)

    for k in keys:
        status, http_status, ratelimit = fw_keys.probe_key(k)
        fp = _sha256_hex(k)
        state[fp] = {
            "status": str(status or "unknown"),
            "last_checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "last_http_status": int(http_status) if isinstance(http_status, int) else None,
            "ratelimit": ratelimit if isinstance(ratelimit, dict) and ratelimit else None,
            "note": None,
        }

    _write_fw_state_file(state_path, keys_obj=state)
    return _get_fireworks_pool_status(pool)


def _parse_dt(obj: Dict[str, Any]) -> Optional[datetime]:
    ts = obj.get("timestamp")
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(float(ts), tz=timezone.utc)
        except Exception:
            return None
    if isinstance(ts, str) and ts.strip():
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            return None
    ts2 = obj.get("ts")
    if isinstance(ts2, str) and ts2.strip():
        try:
            return datetime.fromisoformat(ts2.replace("Z", "+00:00"))
        except Exception:
            return None
    return None


def _infer_provider(obj: Dict[str, Any]) -> str:
    provider = str(obj.get("provider") or "").strip()
    if provider:
        return provider
    model = str(obj.get("model") or "").strip()
    if model.startswith("or_"):
        return "openrouter"
    chain = obj.get("chain")
    if isinstance(chain, list) and any(str(x).startswith("or_") for x in chain):
        return "openrouter"
    err = str(obj.get("error") or "").lower()
    if "openrouter.ai" in err or "insufficient credits" in err:
        return "openrouter"
    return ""


def _usage_tokens(obj: Dict[str, Any]) -> Tuple[int, int, int]:
    usage = obj.get("usage")
    if not isinstance(usage, dict):
        return (0, 0, 0)
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or 0)
    if total <= 0:
        total = prompt + completion
    return (prompt, completion, total)


def _range_window(range_key: str) -> Tuple[str, Optional[datetime], Optional[datetime]]:
    """
    Returns: (normalized_key, since_utc, until_utc)
    """
    key = (range_key or "").strip().lower()
    now = datetime.now(timezone.utc)
    if key in {"", "today", "today_jst", "jst_today"}:
        jst = timezone(timedelta(hours=9))
        start_local = datetime.now(jst).replace(hour=0, minute=0, second=0, microsecond=0)
        return ("today_jst", start_local.astimezone(timezone.utc), now)
    if key in {"last24h", "last_24h", "24h"}:
        return ("last_24h", now - timedelta(hours=24), now)
    if key in {"last7d", "last_7d", "7d"}:
        return ("last_7d", now - timedelta(days=7), now)
    if key in {"last30d", "last_30d", "30d"}:
        return ("last_30d", now - timedelta(days=30), now)
    if key in {"all", "all_time"}:
        return ("all", None, now)
    # fallback: treat unknown as today_jst to avoid accidental full-scan in UI
    jst = timezone(timedelta(hours=9))
    start_local = datetime.now(jst).replace(hour=0, minute=0, second=0, microsecond=0)
    return ("today_jst", start_local.astimezone(timezone.utc), now)


@dataclass
class _Agg:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_hit_calls: int = 0
    cache_hit_total_tokens: int = 0

    def add(self, obj: Dict[str, Any]):
        p, c, t = _usage_tokens(obj)
        self.calls += 1
        self.prompt_tokens += p
        self.completion_tokens += c
        self.total_tokens += t
        cache = obj.get("cache")
        hit = cache.get("hit") if isinstance(cache, dict) else None
        if hit is True:
            self.cache_hit_calls += 1
            self.cache_hit_total_tokens += t


TASK_LABELS: Dict[str, str] = {
    # Script pipeline
    "script_topic_research": "企画リサーチ（本文の材料集め）",
    "script_outline": "アウトライン生成（章立て）",
    "script_master_plan": "全体設計（骨子/配分）",
    "script_chapter_brief": "章ブリーフ生成（章ごとの要点）",
    "script_chapter_draft": "章本文生成（Aテキスト）",
    "script_enhancement": "本文補強（厚み/具体）",
    "script_review": "本文レビュー（整合/読みやすさ）",
    "script_quality_check": "品質チェック（LLM）",
    "script_cta": "CTA生成",
    "script_semantic_alignment_check": "意味整合チェック（タイトル/サムネ ↔ 台本）",
    # Validation gate
    "script_a_text_quality_judge": "品質ゲート: 判定（Judge）",
    "script_a_text_quality_fix": "品質ゲート: 最小修正（Fix）",
    "script_a_text_quality_shrink": "品質ゲート: 短縮（Shrink）",
    "script_a_text_final_polish": "最終ポリッシュ（表現調整）",
    # Web
    "web_search_openrouter": "Web検索（OpenRouter）",
    # Video / images
    "visual_section_plan": "映像セクション計画（SRT→画像キュー）",
    "visual_prompt_refine": "画像プロンプト整形（Refine）",
}


@router.get("/summary")
def usage_summary(
    range: str = Query("today_jst", description="today_jst | last_24h | last_7d | last_30d | all"),
    top_n: int = Query(12, ge=3, le=50),
    provider: str = Query("", description="Optional filter (e.g. openrouter, azure, codex_exec). Empty=all."),
):
    """
    Aggregate token usage by task/model/provider for a time window.
    Intended for UI dashboards and incident triage (e.g., OpenRouter credit exhaustion).
    """
    key, since, until = _range_window(range)
    provider_filter = str(provider or "").strip().lower()

    totals = _Agg()
    by_provider: Dict[str, _Agg] = defaultdict(_Agg)
    by_task: Dict[str, _Agg] = defaultdict(_Agg)
    by_model: Dict[str, _Agg] = defaultdict(_Agg)
    by_channel: Dict[str, _Agg] = defaultdict(_Agg)
    by_routing: Dict[str, _Agg] = defaultdict(_Agg)
    daily: Dict[str, _Agg] = defaultdict(_Agg)

    non_success_total = 0
    non_success_by_status_code: Counter[str] = Counter()
    non_success_by_task: Counter[str] = Counter()
    non_success_by_provider: Counter[str] = Counter()
    recent_failures: List[Dict[str, Any]] = []

    top_calls: List[Tuple[int, Dict[str, Any]]] = []

    line_count = 0
    for obj in _iter_records():
        line_count += 1
        dt = _parse_dt(obj)
        if since and dt and dt < since:
            continue
        if until and dt and dt > until:
            continue

        status = str(obj.get("status") or "").strip()
        provider = _infer_provider(obj)
        if provider_filter and str(provider).strip().lower() != provider_filter:
            continue
        task = str(obj.get("task") or "").strip()

        if status != "success":
            non_success_total += 1
            status_code = str(obj.get("status_code") or "").strip()
            if status_code:
                non_success_by_status_code[status_code] += 1
            non_success_by_task[task or "(unknown)"] += 1
            non_success_by_provider[provider or "(unknown)"] += 1
            # keep a small ring buffer of recent failures for UI
            if len(recent_failures) < 50:
                recent_failures.append(
                    {
                        "timestamp": dt.isoformat() if dt else None,
                        "status": status or None,
                        "status_code": obj.get("status_code"),
                        "task": task or None,
                        "routing_key": obj.get("routing_key"),
                        "provider": provider or None,
                        "model": obj.get("model"),
                        "error": obj.get("error"),
                    }
                )
            continue

        ptk, ctk, ttk = _usage_tokens(obj)
        totals.add(obj)
        by_provider[provider or "(unknown)"].add(obj)
        by_task[task or "(unknown)"].add(obj)
        by_model[str(obj.get("model") or "(unknown)")].add(obj)

        rk = str(obj.get("routing_key") or "").strip()
        if rk:
            by_routing[rk].add(obj)
            if rk.startswith("CH") and "-" in rk:
                ch = rk.split("-", 1)[0]
                by_channel[ch].add(obj)

        if dt:
            day = dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
            daily[day].add(obj)

        # top single calls by total tokens
        if ttk > 0:
            top_calls.append(
                (
                    ttk,
                    {
                        "timestamp": dt.isoformat() if dt else None,
                        "task": task or None,
                        "task_label": TASK_LABELS.get(task or "", task or None),
                        "routing_key": rk or None,
                        "provider": provider or None,
                        "model": obj.get("model"),
                        "prompt_tokens": ptk,
                        "completion_tokens": ctk,
                        "total_tokens": ttk,
                        "finish_reason": obj.get("finish_reason"),
                    },
                )
            )

    # Sort / trim
    top_calls_sorted = [it for _, it in sorted(top_calls, key=lambda kv: kv[0], reverse=True)[: min(20, top_n * 2)]]

    def _agg_to_dict(a: _Agg) -> Dict[str, Any]:
        return {
            "calls": a.calls,
            "prompt_tokens": a.prompt_tokens,
            "completion_tokens": a.completion_tokens,
            "total_tokens": a.total_tokens,
            "cache_hit_calls": a.cache_hit_calls,
            "cache_hit_total_tokens": a.cache_hit_total_tokens,
        }

    def _top_map(m: Dict[str, _Agg], *, key_name: str) -> List[Dict[str, Any]]:
        items = sorted(m.items(), key=lambda kv: kv[1].total_tokens, reverse=True)
        out = []
        for k, a in items[:top_n]:
            payload = {key_name: k, **_agg_to_dict(a)}
            if key_name == "task":
                payload["label"] = TASK_LABELS.get(k, k)
            out.append(payload)
        return out

    providers_out = _top_map(by_provider, key_name="provider")
    tasks_out = _top_map(by_task, key_name="task")
    models_out = _top_map(by_model, key_name="model")
    channels_out = _top_map(by_channel, key_name="channel")
    routing_out = _top_map(by_routing, key_name="routing_key")

    daily_out = []
    for day in sorted(daily.keys()):
        daily_out.append({"day": day, **_agg_to_dict(daily[day])})

    # Failures: sort by count
    def _counter_to_list(c: Counter[str]) -> List[Dict[str, Any]]:
        return [{"key": k, "count": v} for k, v in c.most_common(top_n)]

    failures_out = {
        "total": non_success_total,
        "by_status_code": _counter_to_list(non_success_by_status_code),
        "by_task": _counter_to_list(non_success_by_task),
        "by_provider": _counter_to_list(non_success_by_provider),
        "recent": list(reversed(recent_failures))[:50],
    }

    mtime = None
    try:
        if LOG_PATH.exists():
            mtime = LOG_PATH.stat().st_mtime
    except Exception:
        mtime = None

    return {
        "range": {
            "key": key,
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
        },
        "log": {
            "path": str(LOG_PATH),
            "line_count": line_count,
            "mtime": mtime,
        },
        "totals": _agg_to_dict(totals),
        "providers": providers_out,
        "tasks": tasks_out,
        "models": models_out,
        "channels": channels_out,
        "routing_keys": routing_out,
        "daily": daily_out,
        "failures": failures_out,
        "top_calls": top_calls_sorted,
    }


@router.get("/overrides")
def get_overrides():
    """
    Return current task overrides (effective), with base/local paths for visibility.

    Notes:
    - Base (tracked): configs/llm_task_overrides.yaml
    - Local (untracked): configs/llm_task_overrides.local.yaml
    - UI writes ONLY to the local file to avoid SSOT drift.
    """
    base: Dict[str, Any] = {"tasks": {}}
    local: Dict[str, Any] = {"tasks": {}}
    try:
        if OVERRIDE_BASE_PATH.exists():
            obj = yaml.safe_load(OVERRIDE_BASE_PATH.read_text()) or {}
            if isinstance(obj, dict):
                base = obj
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read base overrides: {e}")

    try:
        if OVERRIDE_LOCAL_PATH.exists():
            obj = yaml.safe_load(OVERRIDE_LOCAL_PATH.read_text()) or {}
            if isinstance(obj, dict):
                local = obj
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read local overrides: {e}")

    base_tasks = base.get("tasks") if isinstance(base.get("tasks"), dict) else {}
    local_tasks = local.get("tasks") if isinstance(local.get("tasks"), dict) else {}
    effective_tasks = _deep_merge_dict(base_tasks, local_tasks)

    return {
        "schema": "ytm.llm_task_overrides.v1",
        "paths": {"base": str(OVERRIDE_BASE_PATH), "local": str(OVERRIDE_LOCAL_PATH)},
        "counts": {"base": len(base_tasks), "local": len(local_tasks), "effective": len(effective_tasks)},
        "tasks": effective_tasks,
        "local_tasks": local_tasks,
    }


@router.post("/overrides")
def set_overrides(body: Dict[str, Any]):
    """
    Replace task overrides (expects dict with top-level 'tasks').
    Validates model selectors against:
      - configs/llm_router.yaml: models.<model_key>
      - configs/llm_model_codes.yaml: codes.<code>

    Writes ONLY to configs/llm_task_overrides.local.yaml (untracked) to avoid SSOT drift.
    """
    if not isinstance(body, dict) or "tasks" not in body:
        raise HTTPException(status_code=400, detail="Payload must include 'tasks' mapping")
    tasks = body.get("tasks") or {}
    if not isinstance(tasks, dict):
        raise HTTPException(status_code=400, detail="'tasks' must be a mapping")

    allowed = _allowed_llm_selectors()
    for task, conf in tasks.items():
        if not isinstance(conf, dict):
            raise HTTPException(status_code=400, detail=f"Invalid override format for task {task}")
        models = conf.get("models") or []
        if not isinstance(models, list):
            raise HTTPException(status_code=400, detail=f"'models' must be a list for task {task}")
        for m in models:
            if allowed and m not in allowed:
                raise HTTPException(status_code=400, detail=f"Unknown model key {m} for task {task}")

    base_tasks: Dict[str, Any] = {}
    try:
        if OVERRIDE_BASE_PATH.exists():
            base_obj = yaml.safe_load(OVERRIDE_BASE_PATH.read_text()) or {}
            if isinstance(base_obj, dict) and isinstance(base_obj.get("tasks"), dict):
                base_tasks = base_obj.get("tasks") or {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read base overrides: {e}")

    def _normalize(obj: Any) -> Any:
        if obj is None:
            return None
        if isinstance(obj, dict):
            return {str(k): _normalize(v) for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}
        if isinstance(obj, list):
            return [_normalize(v) for v in obj]
        return obj

    local_tasks: Dict[str, Any] = {}
    for task, conf in tasks.items():
        base_conf = base_tasks.get(task)
        base_conf_norm = _normalize(base_conf) if isinstance(base_conf, dict) else _normalize({})
        conf_norm = _normalize(conf)
        if conf_norm != base_conf_norm:
            local_tasks[task] = conf

    try:
        OVERRIDE_LOCAL_PATH.parent.mkdir(parents=True, exist_ok=True)
        OVERRIDE_LOCAL_PATH.write_text(
            yaml.safe_dump({"tasks": local_tasks}, allow_unicode=True, sort_keys=False)
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write overrides: {e}")
    return {"status": "ok", "paths": {"base": str(OVERRIDE_BASE_PATH), "local": str(OVERRIDE_LOCAL_PATH)}, "tasks": tasks, "local_tasks": local_tasks}


@router.get("/models")
def list_models():
    """
    Return available model selectors for overrides.
    - model_key from configs/llm_router.yaml
    - code from configs/llm_model_codes.yaml
    """
    return {"models": sorted(_allowed_llm_selectors())}


def _allowed_llm_selectors() -> set[str]:
    out: set[str] = set()
    try:
        if LLM_ROUTER_CONFIG_PATH.exists():
            data = yaml.safe_load(LLM_ROUTER_CONFIG_PATH.read_text()) or {}
            if isinstance(data, dict) and isinstance(data.get("models"), dict):
                out.update(str(k) for k in data.get("models", {}).keys())
    except Exception:
        pass
    try:
        if LLM_MODEL_CODES_PATH.exists():
            data = yaml.safe_load(LLM_MODEL_CODES_PATH.read_text()) or {}
            if isinstance(data, dict) and isinstance(data.get("codes"), dict):
                out.update(str(k) for k in data.get("codes", {}).keys())
    except Exception:
        pass
    return {s for s in out if s and str(s).strip()}


@router.get("/fireworks/status")
def fireworks_status(pools: str = Query("script,image", description="script,image or script or image")):
    """
    Return Fireworks key pool status (masked keys + last probe result) and active key leases.

    Notes:
    - Uses token-free probe history (`/inference/v1/models`) to classify keys as ok/exhausted/invalid/suspended.
    - Does NOT report exact remaining credits (Fireworks does not expose a public balance endpoint via API key).
    """
    raw = (pools or "").strip().lower()
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        parts = ["script", "image"]

    out_pools: Dict[str, Any] = {}
    for p in parts:
        if p not in {"script", "image"}:
            raise HTTPException(status_code=400, detail=f"invalid pool: {p!r}")
        out_pools[p] = _get_fireworks_pool_status(p)

    leases_raw = fw_keys.list_active_leases()
    now = time.time()
    leases_out: List[Dict[str, Any]] = []
    for ent in leases_raw:
        if not isinstance(ent, dict):
            continue
        exp = ent.get("expires_at")
        expires_in = None
        try:
            expires_in = max(0, int(float(exp) - now)) if exp is not None else None
        except Exception:
            expires_in = None
        leases_out.append(
            {
                "pool": ent.get("pool"),
                "key_fp": ent.get("key_fp"),
                "lease_id": str(ent.get("lease_id") or "")[:8] if ent.get("lease_id") else None,
                "agent": ent.get("agent"),
                "pid": ent.get("pid"),
                "host": ent.get("host"),
                "purpose": ent.get("purpose"),
                "acquired_at": _epoch_to_iso(ent.get("acquired_at")),
                "expires_at": _epoch_to_iso(ent.get("expires_at")),
                "expires_in_sec": expires_in,
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "lease_dir": str(fw_keys.lease_root_dir()),
        "pools": out_pools,
        "leases": leases_out,
    }


@router.post("/fireworks/probe")
def fireworks_probe(
    pool: str = Query("script", description="script | image | all"),
    limit: int = Query(0, ge=0, le=200, description="probe only first N keys (0=all in pool)"),
):
    """
    Probe Fireworks keys (token-free) and update pool state. Returns updated status payload.
    """
    p = (pool or "").strip().lower()
    if p == "all":
        return {
            "script": _probe_fireworks_pool("script", limit=limit),
            "image": _probe_fireworks_pool("image", limit=limit),
        }
    if p not in {"script", "image"}:
        raise HTTPException(status_code=400, detail="pool must be script|image|all")
    return _probe_fireworks_pool(p, limit=limit)


def _safe_load_json(path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace") or "null")
    except Exception:
        return {}


def _stage_llm_calls(status_obj: Dict[str, Any], stage_name: str) -> List[Dict[str, Any]]:
    stages = status_obj.get("stages")
    if not isinstance(stages, dict):
        return []
    stage = stages.get(stage_name)
    if not isinstance(stage, dict):
        return []
    details = stage.get("details")
    if not isinstance(details, dict):
        return []
    calls = details.get("llm_calls")
    if not isinstance(calls, list):
        return []
    out: List[Dict[str, Any]] = []
    seen = set()
    for c in calls:
        if not isinstance(c, dict):
            continue
        provider = str(c.get("provider") or "").strip() or None
        model = str(c.get("model") or "").strip() or None
        task = str(c.get("task") or "").strip() or None
        key = (provider, model, task)
        if key in seen:
            continue
        seen.add(key)
        out.append({"provider": provider, "model": model, "task": task})
    return out


def _validation_llm_meta(status_obj: Dict[str, Any]) -> Dict[str, Any]:
    stages = status_obj.get("stages")
    if not isinstance(stages, dict):
        return {}
    stage = stages.get("script_validation")
    if not isinstance(stage, dict):
        return {}
    details = stage.get("details")
    if not isinstance(details, dict):
        return {}
    gate = details.get("llm_quality_gate")
    if not isinstance(gate, dict):
        return {}

    fix_meta = gate.get("fix_llm_meta")
    if not isinstance(fix_meta, dict):
        fix_meta = {}
    final_polish = gate.get("final_polish")
    if not isinstance(final_polish, dict):
        final_polish = {}
    final_meta = final_polish.get("llm_meta")
    if not isinstance(final_meta, dict):
        final_meta = {}

    def pick(meta: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "provider": meta.get("provider"),
            "model": meta.get("model"),
            "request_id": meta.get("request_id"),
        }

    return {
        "verdict": gate.get("verdict"),
        "round": gate.get("round"),
        "max_rounds": gate.get("max_rounds"),
        "fix": pick(fix_meta) if fix_meta else None,
        "final_polish": {
            "enabled": (final_polish.get("enabled") if isinstance(final_polish.get("enabled"), bool) else None),
            "mode": final_polish.get("mode"),
            "provider": final_meta.get("provider"),
            "model": final_meta.get("model"),
            "request_id": final_meta.get("request_id"),
            "draft_source": final_polish.get("draft_source"),
        }
        if final_polish
        else None,
    }


@router.get("/script-routes")
def script_routes(
    channels: str = Query("", description="Comma-separated channels (e.g. CH10,CH22)"),
    max_videos: int = Query(80, ge=1, le=500),
):
    """
    Summarize which provider/model generated each script (per channel/video).

    This reads `workspaces/scripts/<CH>/<NNN>/status.json` and extracts:
    - script_draft llm_calls
    - script_review llm_calls
    - script_validation quality gate meta (fix/final_polish)
    """
    chs = [c.strip() for c in (channels or "").split(",") if c.strip()]
    if not chs:
        raise HTTPException(status_code=400, detail="channels is required (comma-separated)")

    result_channels: List[Dict[str, Any]] = []
    for ch in chs:
        ch_dir = SCRIPTS_ROOT / ch
        if not ch_dir.exists():
            result_channels.append({"channel": ch, "missing": True, "videos": []})
            continue

        vids: List[str] = []
        for p in sorted(ch_dir.iterdir()):
            if not p.is_dir():
                continue
            name = p.name
            if name.isdigit():
                vids.append(name)
        vids = vids[: int(max_videos)]

        videos_out: List[Dict[str, Any]] = []
        for v in vids:
            status_path = ch_dir / v / "status.json"
            if not status_path.exists():
                videos_out.append(
                    {
                        "video": v,
                        "status": "missing",
                        "mtime": None,
                        "script_draft": [],
                        "script_review": [],
                        "script_validation": None,
                    }
                )
                continue

            st = _safe_load_json(status_path)
            try:
                mtime = float(status_path.stat().st_mtime)
            except Exception:
                mtime = None

            videos_out.append(
                {
                    "video": v,
                    "status": st.get("status"),
                    "mtime": _epoch_to_iso(mtime) if mtime is not None else None,
                    "script_draft": _stage_llm_calls(st, "script_draft"),
                    "script_review": _stage_llm_calls(st, "script_review"),
                    "script_validation": _validation_llm_meta(st) or None,
                }
            )

        result_channels.append({"channel": ch, "missing": False, "videos": videos_out})

    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "channels": result_channels,
    }

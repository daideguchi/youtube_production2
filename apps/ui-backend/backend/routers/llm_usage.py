from __future__ import annotations

from typing import List, Dict, Any, Iterable, Tuple, Optional
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query
import json
import yaml

from factory_common.paths import logs_root, repo_root

LOG_PATH = logs_root() / "llm_usage.jsonl"
OVERRIDE_PATH = repo_root() / "configs" / "llm_task_overrides.yaml"
MODEL_REGISTRY_PATH = repo_root() / "configs" / "llm_model_registry.yaml"
IMAGE_MODELS_PATH = repo_root() / "configs" / "image_models.yaml"

router = APIRouter(prefix="/api/llm-usage", tags=["llm_usage"])


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
    Return current task overrides (YAML content as dict). Empty dict if missing.
    """
    if not OVERRIDE_PATH.exists():
        return {"tasks": {}}
    try:
        data = yaml.safe_load(OVERRIDE_PATH.read_text()) or {}
        if not isinstance(data, dict):
            raise ValueError("override file is not a mapping")
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read overrides: {e}")


@router.post("/overrides")
def set_overrides(body: Dict[str, Any]):
    """
    Replace task overrides (expects dict with top-level 'tasks').
    Validates model keys against llm_model_registry + image_models.
    """
    if not isinstance(body, dict) or "tasks" not in body:
        raise HTTPException(status_code=400, detail="Payload must include 'tasks' mapping")
    tasks = body.get("tasks") or {}
    if not isinstance(tasks, dict):
        raise HTTPException(status_code=400, detail="'tasks' must be a mapping")

    allowed = _allowed_models()
    for task, conf in tasks.items():
        if not isinstance(conf, dict):
            raise HTTPException(status_code=400, detail=f"Invalid override format for task {task}")
        models = conf.get("models") or []
        if not isinstance(models, list):
            raise HTTPException(status_code=400, detail=f"'models' must be a list for task {task}")
        for m in models:
            if allowed and m not in allowed:
                raise HTTPException(status_code=400, detail=f"Unknown model key {m} for task {task}")

    try:
        OVERRIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
        OVERRIDE_PATH.write_text(yaml.safe_dump({"tasks": tasks}, allow_unicode=True, sort_keys=False))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write overrides: {e}")
    return {"status": "ok", "tasks": tasks}


@router.get("/models")
def list_models():
    """
    Return available model keys (llm_model_registry + image_models).
    """
    models = set()
    if MODEL_REGISTRY_PATH.exists():
        try:
            data = yaml.safe_load(MODEL_REGISTRY_PATH.read_text()) or {}
            if isinstance(data, dict):
                models.update(data.get("models", {}).keys())
        except Exception:
            pass
    if IMAGE_MODELS_PATH.exists():
        try:
            data = yaml.safe_load(IMAGE_MODELS_PATH.read_text()) or {}
            if isinstance(data, dict):
                models.update(data.get("models", {}).keys())
        except Exception:
            pass
    return {"models": sorted(models)}


def _allowed_models() -> set:
    return set(list_models()["models"])

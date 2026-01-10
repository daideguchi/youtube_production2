from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

from factory_common.paths import repo_root
from factory_common.routing_lockdown import assert_env_absent, lockdown_active


DEFAULT_CODEX_EXEC_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "auto_enable_when_codex_managed": True,
    "profile": "claude-code",
    "sandbox": "read-only",
    "timeout_s": 180,
    # Optional per-task timeout overrides (seconds). Example:
    #   timeout_s_by_task:
    #     script_chapter_draft: 600
    "timeout_s_by_task": {},
    "model": "",
    "selection": {
        "include_task_prefixes": ["tts_", "visual_"],
        "include_tasks": [],
        "exclude_task_prefixes": [],
        "exclude_tasks": ["image_generation", "web_search_openrouter"],
    },
}

_A_TEXT_BODY_FORBIDDEN_TASKS = {
    # A-text body writing (chapters / assembled*.md)
    "script_chapter_draft",
    "script_cta",
    "script_format",
    # Final A-text overwrites (review/quality gate/fixes/final polish)
    "script_chapter_review",
    "script_semantic_alignment_fix",
}


def _is_a_text_body_task(task: str) -> bool:
    t = str(task or "").strip()
    if not t:
        return False
    if t.startswith("script_a_text_"):
        return True
    return t in _A_TEXT_BODY_FORBIDDEN_TASKS


def _truthy(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    return s in {"1", "true", "yes", "y", "on"}


def _env(name: str) -> str:
    return str(os.getenv(name) or "").strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return int(default)
    try:
        return int(raw)
    except Exception:
        return int(default)


def _codex_exec_required_for_task(task: str) -> bool:
    """
    Fixed policy:
    - For TTS pipeline tasks (`tts_*`), when the operator explicitly selects exec-slot=1,
      Codex exec must be used and MUST NOT fall back to LLM APIs.
    """
    t = str(task or "").strip()
    if not t.startswith("tts_"):
        return False
    try:
        from factory_common.llm_exec_slots import active_llm_exec_slot_id

        slot = int(active_llm_exec_slot_id().get("id") or 0)
    except Exception:
        slot = 0
    return slot == 1


def _raise_codex_exec_required(task: str, *, reason: str, meta: Dict[str, Any] | None = None) -> None:
    meta = meta or {}
    err = str(meta.get("error") or "").strip()
    details = []
    if err:
        details.append(f"- error: {err}")
    if meta.get("returncode") is not None:
        details.append(f"- returncode: {meta.get('returncode')}")
    raise SystemExit(
        "\n".join(
            [
                "[POLICY] tts_* tasks require Codex exec in exec-slot=1 (no LLM API fallback).",
                f"- task: {str(task or '').strip()}",
                f"- reason: {reason}",
                *details,
                "- action:",
                "  - Fix Codex exec environment (login/rate-limit) and rerun with the same command",
                "  - Debug only: rerun with LLM_EXEC_SLOT=2 (force codex off) if you explicitly accept API execution",
            ]
        )
    )


def _strip_code_fences(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9_-]*\\n?", "", s).strip()
        if s.endswith("```"):
            s = s[: -3].rstrip()
    return s


def _extract_json_value(text: str) -> Any | None:
    s = _strip_code_fences(text)
    if not s:
        return None
    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\\[{]", s):
        try:
            obj, _end = decoder.raw_decode(s[match.start() :])
        except Exception:
            continue
        if isinstance(obj, (dict, list)):
            return obj
    return None


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)  # type: ignore[arg-type]
        else:
            out[k] = v
    return out


@lru_cache(maxsize=1)
def load_codex_exec_config() -> Dict[str, Any]:
    repo = repo_root()
    local_path = repo / "configs" / "codex_exec.local.yaml"
    default_path = repo / "configs" / "codex_exec.yaml"
    path = local_path if local_path.exists() else default_path
    if not path.exists():
        return dict(DEFAULT_CODEX_EXEC_CONFIG)
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        loaded = {}
    if not isinstance(loaded, dict):
        loaded = {}
    return _deep_merge(dict(DEFAULT_CODEX_EXEC_CONFIG), loaded)


def _codex_exec_globally_enabled(cfg: Dict[str, Any]) -> bool:
    assert_env_absent(
        [
            "YTM_CODEX_EXEC_DISABLE",
            "YTM_CODEX_EXEC_ENABLED",
            "YTM_CODEX_EXEC_PROFILE",
            "YTM_CODEX_EXEC_SANDBOX",
            "YTM_CODEX_EXEC_TIMEOUT_S",
            "YTM_CODEX_EXEC_MODEL",
            "YTM_CODEX_EXEC_EXCLUDE_TASKS",
            "YTM_CODEX_EXEC_ENABLE_IN_PYTEST",
        ],
        context="codex_exec_layer._codex_exec_globally_enabled",
        hint="Use LLM_EXEC_SLOT=1/2 (codex on/off) and configs/codex_exec.yaml for defaults.",
    )
    if _truthy(_env("YTM_CODEX_EXEC_DISABLE")):
        return False

    # pytest safety: avoid spawning Codex during unit tests unless explicitly allowed.
    if os.getenv("PYTEST_CURRENT_TEST") and not _truthy(_env("YTM_CODEX_EXEC_ENABLE_IN_PYTEST")):
        return False

    enabled_override = os.getenv("YTM_CODEX_EXEC_ENABLED")
    if enabled_override is not None and str(enabled_override).strip() != "":
        return _truthy(enabled_override)

    # Optional: numeric exec-slot override (LLM_EXEC_SLOT) when no explicit env override exists.
    try:
        from factory_common.llm_exec_slots import codex_exec_enabled_override

        slot_override = codex_exec_enabled_override()
    except Exception:
        slot_override = None
    if slot_override is not None:
        return bool(slot_override)

    # Under routing lockdown, Codex exec must be explicitly enabled per-run via exec-slot.
    # (Prevents surprise subscription calls in Codex-managed shells.)
    if lockdown_active():
        return False

    if _truthy(cfg.get("enabled")):
        return True

    if _truthy(cfg.get("auto_enable_when_codex_managed")) and _truthy(_env("CODEX_MANAGED_BY_NPM")):
        return True

    return False


def should_try_codex_exec(task: str, *, cfg: Dict[str, Any] | None = None) -> bool:
    cfg = cfg or load_codex_exec_config()
    if not _codex_exec_globally_enabled(cfg):
        return False

    selection = cfg.get("selection") or {}
    include_tasks = set(selection.get("include_tasks") or [])
    include_prefixes = list(selection.get("include_task_prefixes") or [])
    exclude_tasks = set(selection.get("exclude_tasks") or [])
    exclude_prefixes = list(selection.get("exclude_task_prefixes") or [])

    t = str(task or "").strip()
    if not t:
        return False

    # Hard safety rule (fixed):
    # - NEVER route script pipeline tasks through Codex exec.
    #   Script generation must remain LLM API (Fireworks) only to prevent style/model drift.
    if t.startswith("script_"):
        return False

    # Hard safety rule (fixed):
    # - NEVER route any task that writes/overwrites the A-text body through Codex exec.
    #   This is structural (independent of repo config) to prevent accidental drift.
    if _is_a_text_body_task(t):
        return False

    # Operator override (fast rollback of a single task without editing repo config).
    # Example: YTM_CODEX_EXEC_EXCLUDE_TASKS=script_chapter_draft
    raw_exclude = _env("YTM_CODEX_EXEC_EXCLUDE_TASKS")
    if raw_exclude:
        for part in raw_exclude.split(","):
            p = part.strip()
            if p:
                exclude_tasks.add(p)

    if t in exclude_tasks:
        return False
    if any(t.startswith(p) for p in exclude_prefixes):
        return False

    if t in include_tasks:
        return True
    if any(t.startswith(p) for p in include_prefixes):
        return True

    return False


def _extract_text_content(content: Any) -> str | None:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # OpenAI-style multimodal parts: [{"type":"text","text":"..."}, {"type":"image_url", ...}]
        parts: List[str] = []
        for part in content:
            if isinstance(part, dict):
                typ = str(part.get("type") or "")
                if typ in {"text", "input_text"}:
                    parts.append(str(part.get("text") or ""))
                    continue
                # Unknown part type -> treat as multimodal/unsupported for Codex exec.
                return None
            return None
        return "\n".join([p for p in parts if p]).strip()
    # Fallback: stringify
    return str(content)


def _build_codex_prompt(task: str, messages: List[Dict[str, Any]], *, response_format: str | None) -> str | None:
    want_json = str(response_format or "").strip() == "json_object"
    lines: List[str] = []
    lines.append(f"task: {task}")
    if want_json:
        lines.append("Output JSON only. No markdown. No commentary.")
    lines.append("")

    for m in messages or []:
        role = str(m.get("role") or "").strip().upper() or "UNKNOWN"
        content = _extract_text_content(m.get("content"))
        if content is None:
            return None
        content = content.strip()
        lines.append(f"[{role}]")
        lines.append(content)
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def try_codex_exec(
    *,
    task: str,
    messages: List[Dict[str, Any]],
    response_format: str | None,
    cfg: Dict[str, Any] | None = None,
) -> Tuple[str | None, Dict[str, Any]]:
    """
    Returns (content_or_none, meta).

    On success, `content` is:
    - JSON string when response_format == "json_object" (validated dict/list)
    - Otherwise plain text (trimmed)
    """
    cfg = cfg or load_codex_exec_config()
    required = _codex_exec_required_for_task(task)
    if not should_try_codex_exec(task, cfg=cfg):
        meta = {"attempted": False, "provider": "codex_exec", "task": task, "reason": "disabled_or_not_selected"}
        if required:
            _raise_codex_exec_required(task, reason="codex_exec_disabled_or_not_selected", meta=meta)
        return None, meta

    prompt = _build_codex_prompt(task, messages, response_format=response_format)
    if prompt is None:
        meta = {"attempted": True, "provider": "codex_exec", "task": task, "error": "unsupported_multimodal"}
        if required:
            _raise_codex_exec_required(task, reason="unsupported_multimodal", meta=meta)
        return None, meta

    profile = _env("YTM_CODEX_EXEC_PROFILE") or str(cfg.get("profile") or "").strip()
    sandbox = _env("YTM_CODEX_EXEC_SANDBOX") or str(cfg.get("sandbox") or "read-only").strip()
    timeout_s_override = _env("YTM_CODEX_EXEC_TIMEOUT_S")
    if timeout_s_override:
        timeout_s = _env_int("YTM_CODEX_EXEC_TIMEOUT_S", int(cfg.get("timeout_s") or 180))
    else:
        timeout_s = int(cfg.get("timeout_s") or 180)
        try:
            by_task = cfg.get("timeout_s_by_task") or {}
            if isinstance(by_task, dict):
                v = by_task.get(str(task or "").strip())
                if v is not None and str(v).strip() != "":
                    timeout_s = int(v)
        except Exception:
            timeout_s = int(cfg.get("timeout_s") or 180)
    model = _env("YTM_CODEX_EXEC_MODEL") or str(cfg.get("model") or "").strip()

    repo = repo_root()
    want_json = str(response_format or "").strip() == "json_object"

    with tempfile.TemporaryDirectory(prefix="ytm_codex_exec_") as td:
        out_path = Path(td) / "last_message.txt"
        cmd: List[str] = [
            "codex",
            "exec",
            "--sandbox",
            str(sandbox),
            "-C",
            str(repo),
            "--output-last-message",
            str(out_path),
        ]
        if profile:
            cmd.extend(["--profile", str(profile)])
        if model:
            cmd.extend(["-m", str(model)])
        cmd.append("-")

        start = time.time()
        try:
            proc = subprocess.run(
                cmd,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=int(timeout_s),
                check=False,
            )
        except FileNotFoundError as exc:
            meta = {
                "attempted": True,
                "provider": "codex_exec",
                "task": task,
                "error": "codex_binary_not_found",
                "exception": str(exc),
            }
            if required:
                _raise_codex_exec_required(task, reason="codex_binary_not_found", meta=meta)
            return None, meta
        except Exception as exc:
            meta = {
                "attempted": True,
                "provider": "codex_exec",
                "task": task,
                "error": "codex_exec_failed",
                "exception": str(exc),
            }
            if required:
                _raise_codex_exec_required(task, reason="codex_exec_failed", meta=meta)
            return None, meta
        latency_ms = int((time.time() - start) * 1000)

        if not out_path.exists():
            meta = {
                "attempted": True,
                "provider": "codex_exec",
                "task": task,
                "error": "codex_no_output",
                "returncode": proc.returncode,
                "stderr_tail": (proc.stderr or "")[-2000:],
                "latency_ms": latency_ms,
                "profile": profile or None,
                "model": model or None,
            }
            if required:
                _raise_codex_exec_required(task, reason="codex_no_output", meta=meta)
            return None, meta

        try:
            text = out_path.read_text(encoding="utf-8")
        except Exception:
            text = ""

        if want_json:
            obj = _extract_json_value(text)
            if not isinstance(obj, (dict, list)):
                meta = {
                    "attempted": True,
                    "provider": "codex_exec",
                    "task": task,
                    "error": "codex_invalid_json",
                    "returncode": proc.returncode,
                    "stderr_tail": (proc.stderr or "")[-2000:],
                    "latency_ms": latency_ms,
                    "profile": profile or None,
                    "model": model or None,
                }
                if required:
                    _raise_codex_exec_required(task, reason="codex_invalid_json", meta=meta)
                return None, meta
            content = json.dumps(obj, ensure_ascii=False)
        else:
            content = str(text or "").strip()
            if not content:
                meta = {
                    "attempted": True,
                    "provider": "codex_exec",
                    "task": task,
                    "error": "codex_empty_output",
                    "returncode": proc.returncode,
                    "stderr_tail": (proc.stderr or "")[-2000:],
                    "latency_ms": latency_ms,
                    "profile": profile or None,
                    "model": model or None,
                }
                if required:
                    _raise_codex_exec_required(task, reason="codex_empty_output", meta=meta)
                return None, meta

        return content, {
            "attempted": True,
            "provider": "codex_exec",
            "task": task,
            "ok": True,
            "returncode": proc.returncode,
            "latency_ms": latency_ms,
            "profile": profile or None,
            "model": model or None,
        }

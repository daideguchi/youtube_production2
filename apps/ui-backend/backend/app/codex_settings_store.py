from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml
from fastapi import HTTPException

from backend.app.settings_models import (
    CodexCliConfig,
    CodexCliProfile,
    CodexExecConfig,
    CodexSettingsResponse,
    CodexSettingsUpdate,
)
from factory_common.paths import repo_root as ssot_repo_root
from factory_common.paths import script_pkg_root

logger = logging.getLogger("ui_backend")

PROJECT_ROOT = ssot_repo_root()

CODEX_CONFIG_TOML_PATH = Path.home() / ".codex" / "config.toml"
CODEX_EXEC_CONFIG_PATH = PROJECT_ROOT / "configs" / "codex_exec.yaml"
CODEX_EXEC_LOCAL_CONFIG_PATH = PROJECT_ROOT / "configs" / "codex_exec.local.yaml"

CODEX_SETTINGS_LOCK = threading.Lock()


def _deep_merge_dict(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = dict(base or {})
    for key, value in (overlay or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge_dict(out[key], value)  # type: ignore[arg-type]
        else:
            out[key] = value
    return out


_CODEX_PROFILE_HEADER_RE = re.compile(r"^\s*\[profiles\.(?P<name>[^\]]+)\]\s*$")
_CODEX_PROFILE_KV_RE = re.compile(
    r"^\s*(?P<key>model|model_reasoning_effort)\s*=\s*(?P<value>.+?)\s*(?P<comment>#.*)?$"
)
_ALLOWED_CODEX_REASONING_EFFORT = ["low", "medium", "high", "xhigh"]


def _toml_escape_string(value: str) -> str:
    return str(value).replace("\\\\", "\\\\\\\\").replace('"', '\\"')


def _toml_unquote_string(raw: str) -> str:
    s = str(raw or "").strip()
    # Strip trailing comment, if any (caller may already do this).
    if "#" in s:
        s = s.split("#", 1)[0].rstrip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        inner = s[1:-1]
        # Minimal unescape for common cases.
        inner = inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner
    return s


def _parse_codex_profiles_from_toml(text: str) -> Dict[str, Dict[str, Optional[str]]]:
    profiles: Dict[str, Dict[str, Optional[str]]] = {}
    current: Optional[str] = None
    for line in (text or "").splitlines():
        m = _CODEX_PROFILE_HEADER_RE.match(line)
        if m:
            current = str(m.group("name") or "").strip()
            if current:
                profiles.setdefault(current, {})
            continue
        if not current:
            continue
        kv = _CODEX_PROFILE_KV_RE.match(line)
        if not kv:
            continue
        key = str(kv.group("key") or "").strip()
        value = _toml_unquote_string(kv.group("value") or "")
        if key:
            profiles.setdefault(current, {})[key] = value
    # Normalize keys
    out: Dict[str, Dict[str, Optional[str]]] = {}
    for name, conf in profiles.items():
        out[name] = {
            "model": (conf.get("model") or None),
            "model_reasoning_effort": (conf.get("model_reasoning_effort") or None),
        }
    return out


def _upsert_codex_profile_kv(text: str, *, profile: str, kvs: Dict[str, str]) -> str:
    """Surgical TOML update for `[profiles.<name>]` keeping unrelated content intact."""
    profile = str(profile or "").strip()
    if not profile:
        return text
    want = {k: str(v) for k, v in (kvs or {}).items() if str(v).strip()}
    if not want:
        return text

    lines = (text or "").splitlines(keepends=True)
    start = None
    end = len(lines)
    for i, line in enumerate(lines):
        m = _CODEX_PROFILE_HEADER_RE.match(line.rstrip("\r\n"))
        if not m:
            continue
        name = str(m.group("name") or "").strip()
        if start is None and name == profile:
            start = i
            continue
        if start is not None:
            end = i
            break

    def _format_kv(key: str, value: str, *, indent: str = "", comment: str = "") -> str:
        esc = _toml_escape_string(value)
        tail = f" {comment.strip()}" if comment and comment.strip().startswith("#") else (comment or "")
        return f'{indent}{key} = "{esc}"{tail}\n'

    if start is None:
        # Append a new profile section at the end.
        out = list(lines)
        if out and not out[-1].endswith("\n"):
            out[-1] = out[-1] + "\n"
        if out and out[-1].strip() != "":
            out.append("\n")
        out.append(f"[profiles.{profile}]\n")
        for key, value in want.items():
            out.append(_format_kv(key, value))
        return "".join(out)

    existing_keys: set[str] = set()
    body = []
    for line in lines[start + 1 : end]:
        m = _CODEX_PROFILE_KV_RE.match(line.rstrip("\r\n"))
        if m:
            key = str(m.group("key") or "").strip()
            if key in want:
                indent = re.match(r"^\s*", line).group(0) if line else ""
                comment = m.group("comment") or ""
                body.append(_format_kv(key, want[key], indent=indent, comment=comment))
                existing_keys.add(key)
                continue
            existing_keys.add(key)
        body.append(line)

    # Append missing keys at the end of the profile block.
    for key, value in want.items():
        if key not in existing_keys:
            body.append(_format_kv(key, value))

    return "".join([*lines[: start + 1], *body, *lines[end:]])


def _load_codex_exec_config_docs() -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    base_doc: Dict[str, Any] = {}
    local_doc: Dict[str, Any] = {}
    if CODEX_EXEC_CONFIG_PATH.exists():
        try:
            raw = yaml.safe_load(CODEX_EXEC_CONFIG_PATH.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict):
                base_doc = raw
        except Exception:
            base_doc = {}
    if CODEX_EXEC_LOCAL_CONFIG_PATH.exists():
        try:
            raw = yaml.safe_load(CODEX_EXEC_LOCAL_CONFIG_PATH.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict):
                local_doc = raw
        except Exception:
            local_doc = {}
    return base_doc, local_doc, _deep_merge_dict(base_doc, local_doc)


def _load_codex_exec_config_doc() -> Dict[str, Any]:
    _, _, merged = _load_codex_exec_config_docs()
    return merged


def _write_codex_exec_local_config(patch: Dict[str, Any]) -> None:
    CODEX_EXEC_LOCAL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    current: Dict[str, Any] = {}
    if CODEX_EXEC_LOCAL_CONFIG_PATH.exists():
        try:
            raw = yaml.safe_load(CODEX_EXEC_LOCAL_CONFIG_PATH.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict):
                current = raw
        except Exception:
            current = {}
    merged = _deep_merge_dict(current, patch or {})
    CODEX_EXEC_LOCAL_CONFIG_PATH.write_text(
        yaml.safe_dump(merged, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _load_sources_doc() -> Dict[str, Any]:
    """
    Load channel registry sources (same policy as script_pipeline.runner):
    - primary: repo-root `configs/sources.yaml`
    - overlay: packages/script_pipeline/config/sources.yaml
    """
    global_doc: Dict[str, Any] = {}
    local_doc: Dict[str, Any] = {}
    try:
        raw = yaml.safe_load((PROJECT_ROOT / "configs" / "sources.yaml").read_text(encoding="utf-8")) or {}
        if isinstance(raw, dict):
            global_doc = raw
    except Exception:
        global_doc = {}

    try:
        local_path = script_pkg_root() / "config" / "sources.yaml"
        raw = yaml.safe_load(local_path.read_text(encoding="utf-8")) or {}
        if isinstance(raw, dict):
            local_doc = raw
    except Exception:
        local_doc = {}

    return _deep_merge_dict(global_doc, local_doc)


def _resolve_channel_target_chars(channel_code: str) -> Tuple[int, int]:
    sources = _load_sources_doc()
    channels = sources.get("channels") or {}
    if not isinstance(channels, dict):
        return (8000, 12000)
    entry = channels.get(channel_code.upper()) or {}
    if not isinstance(entry, dict):
        return (8000, 12000)

    def _as_int(value: Any) -> Optional[int]:
        if value is None:
            return None
        if isinstance(value, int):
            return value
        text = str(value).strip()
        if not text:
            return None
        try:
            return int(text)
        except Exception:
            return None

    chars_min = _as_int(entry.get("target_chars_min")) or 8000
    chars_max = _as_int(entry.get("target_chars_max")) or 12000
    if chars_max < chars_min:
        chars_max = chars_min
    return (chars_min, chars_max)


def _resolve_channel_chapter_count(channel_code: str) -> Optional[int]:
    sources = _load_sources_doc()
    channels = sources.get("channels") or {}
    if not isinstance(channels, dict):
        return None
    entry = channels.get(channel_code.upper()) or {}
    if not isinstance(entry, dict):
        return None
    raw = entry.get("chapter_count")
    if raw is None:
        return None
    try:
        value = int(str(raw).strip())
    except Exception:
        return None
    return value if value >= 1 else None


def _build_codex_settings_response() -> CodexSettingsResponse:
    base_doc, local_doc, exec_doc = _load_codex_exec_config_docs()

    env_profile = (os.getenv("YTM_CODEX_EXEC_PROFILE") or "").strip()
    env_model = (os.getenv("YTM_CODEX_EXEC_MODEL") or "").strip()
    base_profile = str(base_doc.get("profile") or "").strip()
    local_profile = str(local_doc.get("profile") or "").strip()
    base_model = str(base_doc.get("model") or "").strip()
    local_model = str(local_doc.get("model") or "").strip()

    effective_profile = env_profile or local_profile or base_profile or "claude-code"
    effective_model = env_model or local_model or base_model or ""
    profile_source = "env" if env_profile else ("local" if local_profile else ("base" if base_profile else "default"))
    model_source = "env" if env_model else ("local" if local_model else ("base" if base_model else "default"))

    codex_exec = CodexExecConfig(
        profile=effective_profile,
        model=effective_model or None,
        sandbox=str(exec_doc.get("sandbox") or "").strip() or None,
        timeout_s=int(exec_doc.get("timeout_s") or 0) or None,
        profile_source=profile_source,
        model_source=model_source,
        local_config_path=str(CODEX_EXEC_LOCAL_CONFIG_PATH),
        base_config_path=str(CODEX_EXEC_CONFIG_PATH),
    )

    cli_exists = CODEX_CONFIG_TOML_PATH.exists()
    profiles: Dict[str, Dict[str, Optional[str]]] = {}
    if cli_exists:
        try:
            text = CODEX_CONFIG_TOML_PATH.read_text(encoding="utf-8")
            profiles = _parse_codex_profiles_from_toml(text)
        except Exception:
            profiles = {}
    cli_profiles = [
        CodexCliProfile(
            name=name,
            model=(conf.get("model") if isinstance(conf, dict) else None),
            model_reasoning_effort=(conf.get("model_reasoning_effort") if isinstance(conf, dict) else None),
        )
        for name, conf in sorted(profiles.items(), key=lambda kv: kv[0])
    ]
    cli = CodexCliConfig(
        config_path=str(CODEX_CONFIG_TOML_PATH),
        exists=cli_exists,
        profiles=cli_profiles,
    )

    active_conf = profiles.get(effective_profile, {}) if isinstance(profiles, dict) else {}
    active_profile = CodexCliProfile(
        name=effective_profile,
        model=(active_conf.get("model") if isinstance(active_conf, dict) else None),
        model_reasoning_effort=(active_conf.get("model_reasoning_effort") if isinstance(active_conf, dict) else None),
    )
    return CodexSettingsResponse(
        codex_exec=codex_exec,
        codex_cli=cli,
        active_profile=active_profile,
        allowed_reasoning_effort=list(_ALLOWED_CODEX_REASONING_EFFORT),
    )


def get_codex_settings() -> CodexSettingsResponse:
    return _build_codex_settings_response()


def update_codex_settings(payload: CodexSettingsUpdate) -> CodexSettingsResponse:
    with CODEX_SETTINGS_LOCK:
        # Update pipeline config (configs/codex_exec.local.yaml)
        exec_doc = _load_codex_exec_config_doc()
        current_profile = (
            (os.getenv("YTM_CODEX_EXEC_PROFILE") or "").strip()
            or str(exec_doc.get("profile") or "").strip()
            or "claude-code"
        )
        profile = payload.profile.strip() if isinstance(payload.profile, str) else current_profile
        patch: Dict[str, Any] = {}
        if payload.profile is not None:
            if not profile:
                raise HTTPException(status_code=400, detail="profile は必須です。")
            patch["profile"] = profile
        if payload.model is not None:
            patch["model"] = (payload.model or "").strip()
        if patch:
            _write_codex_exec_local_config(patch)

        # Update Codex CLI profile ( ~/.codex/config.toml )
        cli_profile = (payload.cli_profile or "").strip() or profile or current_profile or "claude-code"
        kvs: Dict[str, str] = {}
        if payload.model_reasoning_effort is not None:
            eff = str(payload.model_reasoning_effort).strip().lower()
            if eff not in _ALLOWED_CODEX_REASONING_EFFORT:
                raise HTTPException(
                    status_code=400,
                    detail=f"model_reasoning_effort は {', '.join(_ALLOWED_CODEX_REASONING_EFFORT)} のいずれかです。",
                )
            kvs["model_reasoning_effort"] = eff
        if payload.cli_model is not None:
            model = str(payload.cli_model or "").strip()
            if model:
                kvs["model"] = model
        if kvs:
            if not CODEX_CONFIG_TOML_PATH.exists():
                raise HTTPException(status_code=404, detail=f"Codex設定が見つかりません: {CODEX_CONFIG_TOML_PATH}")
            try:
                original = CODEX_CONFIG_TOML_PATH.read_text(encoding="utf-8")
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"Codex設定の読み込みに失敗しました: {exc}") from exc

            updated = _upsert_codex_profile_kv(original, profile=cli_profile, kvs=kvs)
            if updated != original:
                # Keep a single rolling backup (no SSOT noise; user-home only).
                try:
                    backup_path = CODEX_CONFIG_TOML_PATH.with_name(CODEX_CONFIG_TOML_PATH.name + ".bak")
                    backup_path.write_text(original, encoding="utf-8")
                except Exception:
                    pass
                try:
                    CODEX_CONFIG_TOML_PATH.write_text(updated, encoding="utf-8")
                except Exception as exc:
                    raise HTTPException(status_code=500, detail=f"Codex設定の書き込みに失敗しました: {exc}") from exc

    return _build_codex_settings_response()

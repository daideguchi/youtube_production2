from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from backend.app.channel_info_store import rebuild_channel_catalog, refresh_channel_info
from backend.app.channel_profile import (
    _build_channel_profile_response,
    _load_channel_info_payload,
    _load_voice_config_payload,
)
from backend.app.channels_models import ChannelProfileResponse, ChannelProfileUpdateRequest
from backend.app.datetime_utils import current_timestamp
from backend.app.lock_store import write_text_with_lock
from backend.app.normalize import normalize_channel_code
from factory_common.paths import logs_root as ssot_logs_root
from factory_common.youtube_handle import (
    YouTubeHandleResolutionError,
    normalize_youtube_handle,
    resolve_youtube_channel_id_from_handle,
)

logger = logging.getLogger(__name__)

CHANNEL_PROFILE_LOG_DIR = ssot_logs_root() / "regression"


def _sanitize_script_prompt(value: str) -> str:
    normalized = value.replace("\r\n", "\n").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="script_prompt を入力してください。")
    if "///" in normalized:
        raise HTTPException(status_code=400, detail="script_prompt に '///' は使用できません。")
    return normalized


def _clean_default_tags(values: Optional[List[str]]) -> Optional[List[str]]:
    if values is None:
        return None
    cleaned: List[str] = []
    for raw in values:
        if raw is None:
            continue
        tag = raw.strip()
        if not tag:
            continue
        if len(tag) > 64:
            raise HTTPException(status_code=400, detail=f"タグが長すぎます: {tag[:32]}…")
        cleaned.append(tag)
    if len(cleaned) > 50:
        raise HTTPException(status_code=400, detail="タグは最大50件までです。")
    return cleaned


def _normalize_youtube_handle_key(value: str) -> str:
    return normalize_youtube_handle(value).lower()


def _ensure_unique_youtube_handle(channel_code: str, handle: str, channel_info_map: Dict[str, dict]) -> None:
    """
    Ensure a YouTube handle maps to exactly one internal channel (accident prevention).
    """

    target = _normalize_youtube_handle_key(handle)
    conflicts: List[str] = []
    for code, info in (channel_info_map or {}).items():
        if code.upper() == channel_code.upper():
            continue
        youtube_info = info.get("youtube") or {}
        other = youtube_info.get("handle") or youtube_info.get("custom_url") or ""
        if not other:
            continue
        try:
            other_key = _normalize_youtube_handle_key(str(other))
        except Exception:
            continue
        if other_key == target:
            conflicts.append(code.upper())
    if conflicts:
        conflicts_s = ", ".join(sorted(set(conflicts)))
        raise HTTPException(
            status_code=400,
            detail=f"YouTubeハンドル {normalize_youtube_handle(handle)} が複数チャンネルに重複しています: {conflicts_s}",
        )


def _checksum_text(value: Optional[str]) -> str:
    text = value or ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _record_change(
    changes: List[Dict[str, Any]],
    field: str,
    old_value: Any,
    new_value: Any,
    *,
    redact: bool = False,
) -> None:
    if old_value == new_value:
        return
    if redact:
        entry = {
            "field": field,
            "old_len": len(old_value or ""),
            "new_len": len(new_value or ""),
            "old_checksum": _checksum_text(old_value),
            "new_checksum": _checksum_text(new_value),
        }
    else:
        entry = {"field": field, "old": old_value, "new": new_value}
    changes.append(entry)


def _append_channel_profile_log(channel_code: str, changes: List[Dict[str, Any]]) -> None:
    if not changes:
        return
    log_dir = CHANNEL_PROFILE_LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"channel_profile_edit_{datetime.now(timezone.utc):%Y%m%d}.log"
    entry = {
        "timestamp": current_timestamp(),
        "channel_code": channel_code,
        "changes": changes,
    }
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def update_channel_profile(channel: str, payload: ChannelProfileUpdateRequest) -> ChannelProfileResponse:
    channel_code = normalize_channel_code(channel)
    info_path, info_payload, channel_dir = _load_channel_info_payload(channel_code)
    script_prompt_path = channel_dir / "script_prompt.txt"
    changes: List[Dict[str, Any]] = []
    info_changed = False

    if payload.description is not None:
        new_description = payload.description.strip()
        if info_payload.get("description") != new_description:
            _record_change(changes, "description", info_payload.get("description"), new_description)
            info_payload["description"] = new_description
            info_changed = True

    if payload.script_prompt is not None:
        sanitized_prompt = _sanitize_script_prompt(payload.script_prompt)
        normalized_json_prompt = sanitized_prompt
        existing_prompt = (info_payload.get("script_prompt") or "").strip()
        if existing_prompt != normalized_json_prompt:
            _record_change(changes, "script_prompt", existing_prompt, normalized_json_prompt, redact=True)
            info_payload["script_prompt"] = normalized_json_prompt
            write_text_with_lock(script_prompt_path, sanitized_prompt + "\n")
            info_changed = True

    if payload.default_tags is not None:
        cleaned_tags = _clean_default_tags(payload.default_tags) or []
        current_tags = info_payload.get("default_tags") or []
        if cleaned_tags != current_tags:
            _record_change(changes, "default_tags", current_tags, cleaned_tags)
            if cleaned_tags:
                info_payload["default_tags"] = cleaned_tags
            else:
                info_payload.pop("default_tags", None)
            info_changed = True

    if "benchmarks" in payload.model_fields_set:
        if payload.benchmarks is None:
            if "benchmarks" in info_payload:
                _record_change(changes, "benchmarks", info_payload.get("benchmarks"), None)
                info_payload.pop("benchmarks", None)
                info_changed = True
        else:
            bench_dump = payload.benchmarks.model_dump()
            bench_dump["updated_at"] = datetime.now().strftime("%Y-%m-%d")
            bench_dump["channels"] = sorted(bench_dump.get("channels") or [], key=lambda it: (it.get("handle") or ""))
            bench_dump["script_samples"] = sorted(
                bench_dump.get("script_samples") or [],
                key=lambda it: (it.get("base") or "", it.get("path") or ""),
            )
            current_bench = info_payload.get("benchmarks")
            if bench_dump != current_bench:
                _record_change(changes, "benchmarks", current_bench, bench_dump)
                info_payload["benchmarks"] = bench_dump
                info_changed = True

    youtube_info = info_payload.setdefault("youtube", {})
    if payload.youtube_title is not None:
        new_title = payload.youtube_title.strip()
        if youtube_info.get("title") != new_title:
            _record_change(changes, "youtube.title", youtube_info.get("title"), new_title)
            youtube_info["title"] = new_title
            info_changed = True
        info_payload.pop("youtube_title", None)
    if payload.youtube_description is not None:
        new_desc = payload.youtube_description.strip()
        current_desc = info_payload.get("youtube_description") or ""
        if new_desc:
            if current_desc != new_desc:
                _record_change(changes, "youtube_description", current_desc or None, new_desc)
                info_payload["youtube_description"] = new_desc
                info_changed = True
        else:
            if "youtube_description" in info_payload:
                _record_change(changes, "youtube_description", current_desc or None, None)
                info_payload.pop("youtube_description", None)
                info_changed = True
    if payload.youtube_handle is not None:
        new_handle_raw = payload.youtube_handle.strip()
        if new_handle_raw:
            try:
                normalized_handle = normalize_youtube_handle(new_handle_raw)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"YouTubeハンドルが不正です: {exc}") from exc

            channel_info_map = refresh_channel_info(force=True)
            _ensure_unique_youtube_handle(channel_code, normalized_handle, channel_info_map)

            try:
                resolved = resolve_youtube_channel_id_from_handle(normalized_handle)
            except YouTubeHandleResolutionError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"YouTubeハンドルから channel_id を特定できませんでした: {exc}",
                ) from exc

            if youtube_info.get("handle") != resolved.handle:
                _record_change(changes, "youtube.handle", youtube_info.get("handle"), resolved.handle)
                youtube_info["handle"] = resolved.handle
                youtube_info["custom_url"] = resolved.handle
                info_changed = True
            if youtube_info.get("channel_id") != resolved.channel_id:
                _record_change(changes, "youtube.channel_id", youtube_info.get("channel_id"), resolved.channel_id)
                youtube_info["channel_id"] = resolved.channel_id
                info_changed = True
            if youtube_info.get("url") != resolved.url:
                _record_change(changes, "youtube.url", youtube_info.get("url"), resolved.url)
                youtube_info["url"] = resolved.url
                info_changed = True
            if youtube_info.get("source") != resolved.channel_id:
                _record_change(changes, "youtube.source", youtube_info.get("source"), resolved.channel_id)
                youtube_info["source"] = resolved.channel_id
                info_changed = True
            if resolved.title and youtube_info.get("title") != resolved.title:
                _record_change(changes, "youtube.title", youtube_info.get("title"), resolved.title)
                youtube_info["title"] = resolved.title
                info_changed = True

            branding_info = info_payload.setdefault("branding", {})
            if branding_info.get("handle") != resolved.handle:
                _record_change(changes, "branding.handle", branding_info.get("handle"), resolved.handle)
                branding_info["handle"] = resolved.handle
                branding_info["custom_url"] = resolved.handle
                info_changed = True
            if branding_info.get("url") != resolved.url:
                _record_change(changes, "branding.url", branding_info.get("url"), resolved.url)
                branding_info["url"] = resolved.url
                info_changed = True
            if resolved.title and branding_info.get("title") != resolved.title:
                _record_change(changes, "branding.title", branding_info.get("title"), resolved.title)
                branding_info["title"] = resolved.title
                info_changed = True
            if resolved.avatar_url and branding_info.get("avatar_url") != resolved.avatar_url:
                _record_change(
                    changes,
                    "branding.avatar_url",
                    branding_info.get("avatar_url"),
                    resolved.avatar_url,
                )
                branding_info["avatar_url"] = resolved.avatar_url
                info_changed = True
        else:
            # Allow clearing handle explicitly.
            if youtube_info.get("handle"):
                _record_change(changes, "youtube.handle", youtube_info.get("handle"), None)
                youtube_info.pop("handle", None)
                youtube_info.pop("custom_url", None)
                info_changed = True
        info_payload.pop("youtube_handle", None)

    audio_changed = False
    if payload.audio:
        config_path, voice_payload = _load_voice_config_payload(channel_code, required=True)
        voices = (voice_payload.get("voices") or {}).keys()
        if payload.audio.default_voice_key is not None:
            new_key = payload.audio.default_voice_key.strip()
            if new_key not in voices:
                raise HTTPException(
                    status_code=400,
                    detail=f"voice_config.json に {new_key} が定義されていません。",
                )
            if voice_payload.get("default_voice_key") != new_key:
                _record_change(
                    changes,
                    "audio.default_voice_key",
                    voice_payload.get("default_voice_key"),
                    new_key,
                )
                voice_payload["default_voice_key"] = new_key
                audio_changed = True
        if payload.audio.section_voice_rules is not None:
            cleaned_rules: Dict[str, str] = {}
            for section, key in payload.audio.section_voice_rules.items():
                if section is None or key is None:
                    continue
                section_name = section.strip()
                voice_key = key.strip()
                if not section_name or not voice_key:
                    continue
                if voice_key not in voices:
                    raise HTTPException(
                        status_code=400,
                        detail=f"voice_config.json に {voice_key} が定義されていません。",
                    )
                cleaned_rules[section_name] = voice_key
            current_rules = voice_payload.get("section_voice_rules") or {}
            if cleaned_rules != current_rules:
                _record_change(
                    changes,
                    "audio.section_voice_rules",
                    current_rules,
                    cleaned_rules,
                )
                voice_payload["section_voice_rules"] = cleaned_rules
                audio_changed = True
        if audio_changed and config_path is not None:
            write_text_with_lock(
                config_path, json.dumps(voice_payload, ensure_ascii=False, indent=2) + "\n"
            )

    if info_changed:
        write_text_with_lock(info_path, json.dumps(info_payload, ensure_ascii=False, indent=2) + "\n")
        rebuild_channel_catalog()

    if info_changed or audio_changed:
        _append_channel_profile_log(channel_code, changes)

    return _build_channel_profile_response(channel_code)


#!/usr/bin/env python3
"""
Create a pre-production "Production Pack" snapshot for a single episode.

Why:
- Stabilize reproducibility: what inputs/configs were used for CHxx/NNN?
- Provide a deterministic QA gate (pass/warn/fail) before running expensive steps.
- Leave an audit trail under workspaces/logs/regression/production_pack/.
- When --write-latest is used, also emit a diff against the previous latest pack.

Usage:
  python3 scripts/ops/production_pack.py --channel CH01 --video 001
  python3 scripts/ops/production_pack.py --channel CH02 --video 24 --write-latest
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

from _bootstrap import bootstrap

PROJECT_ROOT = bootstrap(load_env=False)

from factory_common.paths import (
    channels_csv_path,
    logs_root,
    persona_path,
    planning_root,
    research_root,
    repo_root,
    script_pkg_root,
    script_data_root,
    thumbnails_root,
    thumbnail_assets_dir,
    video_pkg_root,
)
from preproduction_issue_catalog import fix_hints_for_issue


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _normalize_channel(ch: str) -> str:
    s = (ch or "").strip().upper()
    if re.fullmatch(r"CH\d{2}", s):
        return s
    m = re.fullmatch(r"CH(\d+)", s)
    if m:
        return f"CH{int(m.group(1)):02d}"
    return s


def _normalize_video(video: str) -> str:
    s = (video or "").strip()
    try:
        return f"{int(s):03d}"
    except Exception:
        return s.zfill(3) if s.isdigit() else s


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _file_meta(path: Path) -> dict[str, Any]:
    try:
        st = path.stat()
        return {
            "path": str(path),
            "exists": True,
            "size_bytes": st.st_size,
            "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
            "sha256": _sha256(path),
        }
    except Exception:
        return {"path": str(path), "exists": False}


def _deep_merge_dict(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = dict(base or {})
    for k, v in (overlay or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge_dict(out[k], v)  # type: ignore[arg-type]
        else:
            out[k] = v
    return out


def _load_yaml_optional(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _load_sources_doc() -> tuple[dict[str, Any], str | None]:
    """
    Mirror runner behavior:
      - primary: repo-root `configs/sources.yaml`
      - overlay: `packages/script_pipeline/config/sources.yaml`
    """
    global_path = repo_root() / "configs" / "sources.yaml"
    local_path = script_pkg_root() / "config" / "sources.yaml"
    try:
        base = _load_yaml_optional(global_path)
        overlay = _load_yaml_optional(local_path)
        return _deep_merge_dict(base, overlay), None
    except Exception as e:
        return {}, repr(e)


def _json_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    return str(obj)


def _read_csv_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return (reader.fieldnames or []), list(reader)


def _video_number_from_row(row: dict[str, str]) -> str:
    for key in ("動画番号", "No.", "VideoNumber", "video_number", "video"):
        if key in row and (row.get(key) or "").strip():
            raw = (row.get(key) or "").strip()
            try:
                return f"{int(raw):03d}"
            except Exception:
                return raw
    for key in ("動画ID", "台本番号", "ScriptID", "script_id"):
        v = (row.get(key) or "").strip()
        m = re.search(r"\bCH\d{2}-(\d{3})\b", v)
        if m:
            return m.group(1)
    return "???"


def _script_id_from_row(row: dict[str, str]) -> str | None:
    for key in ("動画ID", "台本番号", "ScriptID", "script_id"):
        v = (row.get(key) or "").strip()
        if re.fullmatch(r"CH\d{2}-\d{3}", v):
            return v
    return None


def _find_planning_row(csv_path: Path, channel: str, video: str) -> dict[str, Any]:
    headers, rows = _read_csv_rows(csv_path)
    target_sid = f"{channel}-{video}"

    for idx, row in enumerate(rows, start=1):
        row_video = _normalize_video(_video_number_from_row(row))
        row_sid = _script_id_from_row(row)
        if row_video == video or row_sid == target_sid:
            return {"ok": True, "row_index": idx, "headers": headers, "row": row}

    return {"ok": False, "row_index": None, "headers": headers, "row": None}


def _find_script_channel_dir(channel: str) -> dict[str, Any]:
    base = repo_root() / "packages" / "script_pipeline" / "channels"
    matches = sorted([p for p in base.glob(f"{channel}-*") if p.is_dir()])
    if not matches:
        return {"ok": False, "matches": []}
    # Prefer exact 1 match; otherwise choose first and keep the full list for audit.
    return {"ok": True, "path": str(matches[0]), "matches": [str(p) for p in matches]}


def _git_head() -> str | None:
    try:
        out = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_root(), stderr=subprocess.DEVNULL)
        return out.decode("utf-8").strip() or None
    except Exception:
        return None


def _add_gate_issue(
    issues: list[dict[str, Any]],
    *,
    severity: str,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    channel: str | None = None,
    video: str | None = None,
) -> None:
    it: dict[str, Any] = {"severity": severity, "code": code, "message": message}
    if details:
        it["details"] = details
    hints = fix_hints_for_issue(code, channel=channel, video=video)
    if hints:
        it["fix_hints"] = hints
    issues.append(it)


def _finalize_gate(issues: list[dict[str, Any]]) -> dict[str, Any]:
    errors = sum(1 for it in issues if isinstance(it, dict) and it.get("severity") == "error")
    warnings = sum(1 for it in issues if isinstance(it, dict) and it.get("severity") == "warning")
    if errors > 0:
        result = "fail"
        score = 0
    elif warnings > 0:
        result = "warn"
        score = max(0, 100 - warnings * 5)
    else:
        result = "pass"
        score = 100
    return {"result": result, "score": score, "counts": {"errors": errors, "warnings": warnings}, "issues": issues}


def _is_published_planning_row(row: dict[str, str] | None) -> bool:
    if not isinstance(row, dict):
        return False
    progress = str(row.get("進捗") or "").strip()
    if not progress:
        return False
    if "投稿済み" in progress:
        return True
    if progress.lower() == "published":
        return True
    return False


def _read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _resolve_benchmark_sample_path(base: str, rel: str) -> Path | None:
    b = (base or "").strip().lower()
    p = (rel or "").strip().lstrip("/").lstrip("\\")
    if not p:
        return None
    if b == "research":
        return research_root() / p
    if b == "scripts":
        return script_data_root() / p
    return None


def _planning_patches_for_target(channel: str, video: str) -> list[dict[str, Any]]:
    """
    Best-effort: detect planning patches that target this episode.

    Patch SoT lives under workspace_root()/planning/patches (tracked).
    """
    base = planning_root() / "patches"
    if not base.exists():
        return []

    out: list[dict[str, Any]] = []
    for p in sorted(base.glob("*.yaml")):
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        if str(data.get("schema") or "").strip() != "ytm.planning_patch.v1":
            continue
        target = data.get("target")
        if not isinstance(target, dict):
            continue
        ch = _normalize_channel(str(target.get("channel") or ""))
        v = _normalize_video(str(target.get("video") or ""))
        if ch != channel or v != video:
            continue
        out.append(
            {
                "patch_id": str(data.get("patch_id") or "").strip(),
                "path": str(p),
                "file": _file_meta(p),
                "apply_keys": sorted(list((data.get("apply") or {}).keys())) if isinstance(data.get("apply"), dict) else [],
            }
        )
    return out


def _diff_packs(
    before: Any,
    after: Any,
    *,
    ignore_paths: set[str],
    _path: str = "",
    _out: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """
    JSON-ish deep diff for audit logs (no external deps).
    """
    out = _out if _out is not None else []

    if _path and _path in ignore_paths:
        return out

    if isinstance(before, dict) and isinstance(after, dict):
        keys = sorted(set(before.keys()) | set(after.keys()))
        for k in keys:
            key = str(k)
            p = f"{_path}.{key}" if _path else key
            if p in ignore_paths:
                continue
            if key not in before:
                out.append({"type": "added", "path": p, "before": None, "after": after.get(key)})
                continue
            if key not in after:
                out.append({"type": "removed", "path": p, "before": before.get(key), "after": None})
                continue
            _diff_packs(before.get(key), after.get(key), ignore_paths=ignore_paths, _path=p, _out=out)
        return out

    # Keep list diffs simple/stable (whole-list compare).
    if isinstance(before, list) and isinstance(after, list):
        if before != after:
            out.append({"type": "changed", "path": _path, "before": before, "after": after})
        return out

    if before != after:
        out.append({"type": "changed", "path": _path, "before": before, "after": after})
    return out


def _build_pack(*, channel: str, video: str) -> dict[str, Any]:
    csv_path = channels_csv_path(channel)
    persona = persona_path(channel)
    planning_template = planning_root() / "templates" / f"{channel}_planning_template.csv"
    sources_global = repo_root() / "configs" / "sources.yaml"
    sources_local = script_pkg_root() / "config" / "sources.yaml"
    script_channels_json = repo_root() / "packages" / "script_pipeline" / "channels" / "channels.json"
    script_templates = script_pkg_root() / "templates.yaml"
    script_stages = script_pkg_root() / "stages.yaml"
    voice_config_json = script_pkg_root() / "audio" / "channels" / channel / "voice_config.json"
    script_channel_prompt_yaml = repo_root() / "packages" / "script_pipeline" / "prompts" / "channels" / f"{channel}.yaml"
    youtube_description_prompt = repo_root() / "packages" / "script_pipeline" / "prompts" / "youtube_description_prompt.txt"
    video_channel_presets = video_pkg_root() / "config" / "channel_presets.json"
    video_templates_registry = video_pkg_root() / "config" / "template_registry.json"
    image_system_prompt = video_pkg_root() / "system_prompt_for_image_generation.txt"
    thumbnail_templates = thumbnails_root() / "templates.json"
    thumbnail_projects = thumbnails_root() / "projects.json"
    thumbnail_assets = thumbnail_assets_dir(channel, video)

    gate_issues: list[dict[str, Any]] = []
    sources_doc, sources_parse_error = _load_sources_doc()
    sources_channels = sources_doc.get("channels") if isinstance(sources_doc, dict) else {}
    sources_channels = sources_channels if isinstance(sources_channels, dict) else {}
    sources_channel = sources_channels.get(channel) if isinstance(sources_channels.get(channel), dict) else None
    sources_globals = sources_doc.get("script_globals") if isinstance(sources_doc.get("script_globals"), dict) else None
    if not sources_global.exists():
        _add_gate_issue(
            gate_issues,
            severity="warning",
            code="missing_sources_yaml",
            message=f"Missing: {sources_global}",
            channel=channel,
            video=video,
        )
    if sources_parse_error:
        _add_gate_issue(
            gate_issues,
            severity="warning",
            code="sources_yaml_parse_error",
            message=sources_parse_error,
            channel=channel,
            video=video,
        )
    if sources_global.exists() and not sources_parse_error and sources_channel is None:
        _add_gate_issue(
            gate_issues,
            severity="warning",
            code="missing_sources_channel_entry",
            message=f"sources.yaml missing channels.{channel}",
            channel=channel,
            video=video,
        )

    if not csv_path.exists():
        _add_gate_issue(
            gate_issues,
            severity="error",
            code="missing_planning_csv",
            message=f"CSV not found: {csv_path}",
            channel=channel,
            video=video,
        )
        planning = {"ok": False, "row": None, "row_index": None, "headers": []}
        planning_lint_report: dict[str, Any] | None = None
        planning_lint_targeted: dict[str, Any] | None = None
    else:
        planning = _find_planning_row(csv_path, channel, video)
        if not planning["ok"]:
            _add_gate_issue(
                gate_issues,
                severity="error",
                code="missing_planning_row",
                message=f"No planning row for {channel}/{video}",
                channel=channel,
                video=video,
            )

        title = ""
        if isinstance(planning.get("row"), dict):
            title = (planning["row"].get("タイトル") or "").strip()
        if not title and planning["ok"]:
            _add_gate_issue(
                gate_issues,
                severity="error",
                code="missing_title",
                message="Planning タイトル is empty",
                channel=channel,
                video=video,
            )

        planning_lint_report = None
        planning_lint_targeted = None
        try:
            import planning_lint as _planning_lint

            planning_lint_report = _planning_lint.lint_planning_csv(csv_path, channel)
            issues_raw = planning_lint_report.get("issues") if isinstance(planning_lint_report, dict) else None
            row_idx = planning.get("row_index")
            targeted: list[dict[str, Any]] = []
            global_errors = 0
            global_warnings = 0
            if isinstance(issues_raw, list):
                for it in issues_raw:
                    if not isinstance(it, dict):
                        continue
                    sev = str(it.get("severity") or "").strip()
                    if sev == "error":
                        global_errors += 1
                    elif sev == "warning":
                        global_warnings += 1

                    it_row = it.get("row_index")
                    it_video = _normalize_video(str(it.get("video") or ""))
                    is_header = str(it_video) == "???" or it_row == 0
                    is_row = row_idx is not None and it_row == row_idx
                    is_video = it_video == video
                    if is_header or is_row or is_video:
                        targeted.append(it)

            planning_lint_targeted = {
                "row_index": row_idx,
                "counts": {"global_errors": global_errors, "global_warnings": global_warnings, "targeted": len(targeted)},
                "issues": targeted,
            }

            for it in targeted:
                sev = str(it.get("severity") or "").strip()
                code = str(it.get("code") or "").strip() or "unknown"
                msg = str(it.get("message") or "").strip()
                if sev not in {"error", "warning"}:
                    continue
                _add_gate_issue(
                    gate_issues,
                    severity=sev,
                    code=f"planning_lint.{code}",
                    message=msg,
                    details={"row_index": it.get("row_index"), "video": it.get("video"), "columns": it.get("columns")},
                    channel=channel,
                    video=video,
                )

            # If planning_lint found errors elsewhere, warn but do not fail this episode.
            if global_errors > 0 and not any(it.get("severity") == "error" for it in targeted):
                _add_gate_issue(
                    gate_issues,
                    severity="warning",
                    code="planning_lint_global_errors",
                    message=f"planning_lint found errors in other rows: {global_errors} errors, {global_warnings} warnings",
                    channel=channel,
                    video=video,
                )
        except Exception as e:
            # Lint failure should not block pack creation, but it should be visible.
            _add_gate_issue(
                gate_issues,
                severity="warning",
                code="planning_lint_exception",
                message=repr(e),
                channel=channel,
                video=video,
            )
            planning_lint_targeted = None

    # Per-episode required fields (planning_requirements policy). This is a warning-level QA signal.
    required_columns_by_policy: list[str] = []
    missing_required_columns: list[str] = []
    missing_required_values: list[str] = []
    if planning.get("ok") and isinstance(planning.get("row"), dict) and not _is_published_planning_row(planning.get("row")):
        try:
            from script_pipeline.tools import planning_requirements

            numeric_video: int | None
            try:
                numeric_video = int(video)
            except Exception:
                numeric_video = None

            required_columns_by_policy = planning_requirements.resolve_required_columns(channel, numeric_video)
            row = planning.get("row") or {}
            if isinstance(row, dict):
                for col in required_columns_by_policy:
                    if col not in row:
                        missing_required_columns.append(col)
                        continue
                    if not str(row.get(col) or "").strip():
                        missing_required_values.append(col)
        except Exception as e:
            _add_gate_issue(
                gate_issues,
                severity="warning",
                code="planning_requirements_exception",
                message=repr(e),
                channel=channel,
                video=video,
            )

    if missing_required_columns or missing_required_values:
        _add_gate_issue(
            gate_issues,
            severity="warning",
            code="missing_required_fields_by_policy",
            message="Planning row is missing required fields by policy (see details)",
            details={
                "required_columns": required_columns_by_policy,
                "missing_columns": missing_required_columns,
                "missing_values": missing_required_values,
            },
            channel=channel,
            video=video,
        )

    if not persona.exists():
        _add_gate_issue(
            gate_issues,
            severity="warning",
            code="missing_persona",
            message=f"Persona not found: {persona}",
            channel=channel,
            video=video,
        )

    if not voice_config_json.exists():
        _add_gate_issue(
            gate_issues,
            severity="error",
            code="missing_voice_config",
            message=f"Missing: {voice_config_json}",
            channel=channel,
            video=video,
        )
    else:
        raw = _read_json(voice_config_json)
        if raw is None:
            _add_gate_issue(
                gate_issues,
                severity="error",
                code="invalid_voice_config_json",
                message=f"Invalid JSON: {voice_config_json}",
                channel=channel,
                video=video,
            )
        elif not isinstance(raw, dict):
            _add_gate_issue(
                gate_issues,
                severity="error",
                code="invalid_voice_config_schema",
                message=f"voice_config.json must be an object: {voice_config_json}",
                channel=channel,
                video=video,
            )

    ch_dir = _find_script_channel_dir(channel)
    script_prompt_path: Path | None = None
    channel_info_path: Path | None = None
    if not ch_dir.get("ok", False):
        _add_gate_issue(
            gate_issues,
            severity="error",
            code="missing_script_channel_dir",
            message=f"Channel dir not found for {channel}",
            channel=channel,
            video=video,
        )
    else:
        script_prompt_path = Path(str(ch_dir["path"])) / "script_prompt.txt"
        channel_info_path = Path(str(ch_dir["path"])) / "channel_info.json"
        if not script_prompt_path.exists():
            _add_gate_issue(
                gate_issues,
                severity="warning",
                code="missing_script_prompt",
                message=f"Missing: {script_prompt_path}",
                channel=channel,
                video=video,
            )
        if channel_info_path and not channel_info_path.exists():
            _add_gate_issue(
                gate_issues,
                severity="warning",
                code="missing_channel_info_json",
                message=f"Missing: {channel_info_path}",
                channel=channel,
                video=video,
            )

    channel_info: dict[str, Any] | None = None
    video_workflow: str | None = None
    benchmarks_summary: dict[str, Any] | None = None
    if channel_info_path and channel_info_path.exists():
        try:
            raw = json.loads(channel_info_path.read_text(encoding="utf-8"))
        except Exception as e:
            _add_gate_issue(
                gate_issues,
                severity="warning",
                code="invalid_channel_info_json",
                message=f"Invalid JSON: {channel_info_path}: {e!r}",
                channel=channel,
                video=video,
            )
        else:
            channel_info = raw if isinstance(raw, dict) else None
            if not isinstance(channel_info, dict):
                _add_gate_issue(
                    gate_issues,
                    severity="warning",
                    code="invalid_channel_info_schema",
                    message=f"channel_info.json must be an object: {channel_info_path}",
                    channel=channel,
                    video=video,
                )
            else:
                vw = str(channel_info.get("video_workflow") or "").strip().lower()
                if vw:
                    video_workflow = vw
                else:
                    _add_gate_issue(
                        gate_issues,
                        severity="warning",
                        code="missing_video_workflow",
                        message=f"channel_info.json missing video_workflow: {channel}",
                        channel=channel,
                        video=video,
                    )

                b = channel_info.get("benchmarks")
                if isinstance(b, dict):
                    samples_raw = b.get("script_samples") or []
                    samples: list[dict[str, Any]] = []
                    if isinstance(samples_raw, list):
                        for s in samples_raw:
                            if not isinstance(s, dict):
                                continue
                            base = str(s.get("base") or "")
                            rel = str(s.get("path") or "")
                            resolved = _resolve_benchmark_sample_path(base, rel)
                            samples.append(
                                {
                                    "base": base,
                                    "path": rel,
                                    "label": s.get("label"),
                                    "note": s.get("note"),
                                    "resolved_path": str(resolved) if resolved else None,
                                    "exists": bool(resolved and resolved.exists()),
                                }
                            )
                    benchmarks_summary = {
                        "version": b.get("version"),
                        "updated_at": b.get("updated_at"),
                        "channels_count": len(b.get("channels") or []) if isinstance(b.get("channels"), list) else None,
                        "script_samples": samples,
                    }

    planning_patches = _planning_patches_for_target(channel, video)

    # Published lock is a strong signal; pack still generates, but warns to avoid accidental re-run.
    if planning.get("ok") and _is_published_planning_row(planning.get("row")):
        _add_gate_issue(
            gate_issues,
            severity="warning",
            code="planning_row_published_lock",
            message="Planning row looks published-locked (進捗 contains 投稿済み/published). Verify before running production steps.",
            channel=channel,
            video=video,
        )

    # Resolve channel preset (best-effort; do not block pack creation on parse failure).
    preset_resolved: dict[str, Any] | None = None
    prompt_template_registered: bool | None = None
    preset_parse_error: str | None = None
    try:
        presets_data = _read_json(video_channel_presets)
        preset_resolved = (
            presets_data.get("channels", {}).get(channel) if isinstance(presets_data, dict) else None  # type: ignore[union-attr]
        )
    except Exception as e:
        preset_parse_error = repr(e)
        preset_resolved = None
    if preset_resolved is None and video_workflow == "capcut":
        _add_gate_issue(
            gate_issues,
            severity="error",
            code="missing_video_channel_preset",
            message=f"Video channel preset not found for {channel} in {video_channel_presets}",
            channel=channel,
            video=video,
        )

    if isinstance(preset_resolved, dict) and preset_resolved.get("persona_required") and not persona.exists():
        _add_gate_issue(
            gate_issues,
            severity="error",
            code="persona_required_but_missing",
            message=f"persona_required=true but persona is missing: {persona}",
            channel=channel,
            video=video,
        )

    prompt_template_raw = str((preset_resolved or {}).get("prompt_template") or "").strip() if isinstance(preset_resolved, dict) else ""
    prompt_template_path: Path | None = None
    if prompt_template_raw:
        pt = Path(prompt_template_raw)
        if pt.is_absolute():
            prompt_template_path = pt
        elif pt.name == prompt_template_raw:
            prompt_template_path = video_pkg_root() / "templates" / pt.name
        else:
            prompt_template_path = video_pkg_root() / prompt_template_raw

    try:
        registry = _read_json(video_templates_registry)
        prompt_template_id = Path(prompt_template_raw).name if prompt_template_raw else ""
        if prompt_template_id and isinstance(registry, dict) and isinstance(registry.get("templates"), list):
            ids = {str(t.get("id") or "") for t in registry["templates"] if isinstance(t, dict)}
            prompt_template_registered = prompt_template_id in ids
    except Exception:
        prompt_template_registered = None

    if video_workflow == "capcut" and isinstance(preset_resolved, dict):
        status = str(preset_resolved.get("status") or "active").strip().lower()
        if status == "active":
            capcut_tpl = str(preset_resolved.get("capcut_template") or "").strip()
            if not capcut_tpl:
                _add_gate_issue(
                    gate_issues,
                    severity="error",
                    code="active_preset_missing_capcut_template",
                    message=f"active preset missing capcut_template: {channel}",
                    channel=channel,
                    video=video,
                )
            if not prompt_template_raw:
                _add_gate_issue(
                    gate_issues,
                    severity="warning",
                    code="missing_prompt_template",
                    message=f"capcut preset is missing prompt_template for {channel} (optional; default template will be used)",
                    channel=channel,
                    video=video,
                )
        if prompt_template_path and not prompt_template_path.exists():
            _add_gate_issue(
                gate_issues,
                severity="error",
                code="missing_prompt_template_file",
                message=f"prompt_template file not found: {prompt_template_path}",
                channel=channel,
                video=video,
            )
        if prompt_template_registered is False:
            _add_gate_issue(
                gate_issues,
                severity="warning",
                code="prompt_template_not_registered",
                message=f"prompt_template not registered in template_registry.json: {Path(prompt_template_raw).name}",
                channel=channel,
                video=video,
            )

    gate = _finalize_gate(gate_issues)

    pack = {
        "schema": "ytm.production_pack.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_head": _git_head(),
        "channel": channel,
        "video": video,
        "script_id": f"{channel}-{video}",
        "planning": {
            "csv": _file_meta(csv_path),
            "row_index": planning.get("row_index"),
            "headers": planning.get("headers"),
            "row": planning.get("row"),
            "row_is_published_lock": _is_published_planning_row(planning.get("row")),
            "required_columns_by_policy": required_columns_by_policy,
            "missing_required_columns_by_policy": missing_required_columns,
            "missing_required_values_by_policy": missing_required_values,
        },
        "planning_patches": planning_patches,
        "resolved": {
            "sources": {
                "global_sources_yaml": _file_meta(sources_global),
                "local_sources_yaml": _file_meta(sources_local),
                "parse_error": sources_parse_error,
                "script_globals": _json_safe(sources_globals),
                "channel": _json_safe(sources_channel),
            },
            "script_pipeline": {
                "channels_json": _file_meta(script_channels_json),
                "channel_dir": ch_dir,
                "script_prompt": _file_meta(script_prompt_path) if script_prompt_path else None,
                "channel_info_json": _file_meta(channel_info_path) if channel_info_path else None,
                "video_workflow": video_workflow,
                "voice_config_json": _file_meta(voice_config_json),
                "templates_yaml": _file_meta(script_templates),
                "stages_yaml": _file_meta(script_stages),
                "channel_prompt_yaml": _file_meta(script_channel_prompt_yaml),
                "youtube_description_prompt": _file_meta(youtube_description_prompt),
            },
            "video_pipeline": {
                "channel_presets_json": _file_meta(video_channel_presets),
                "channel_preset_resolved": preset_resolved,
                "channel_preset_parse_error": preset_parse_error,
                "template_registry_json": _file_meta(video_templates_registry),
                "prompt_template": _file_meta(prompt_template_path) if prompt_template_path else None,
                "prompt_template_registered": prompt_template_registered,
                "image_system_prompt": _file_meta(image_system_prompt),
            },
            "thumbnails": {
                "templates_json": _file_meta(thumbnail_templates),
                "projects_json": _file_meta(thumbnail_projects),
                "assets_dir": {"path": str(thumbnail_assets), "exists": thumbnail_assets.exists()},
            },
        },
        "optional_inputs": {
            "persona": _file_meta(persona),
            "planning_template_csv": _file_meta(planning_template),
            "benchmarks_summary": benchmarks_summary,
        },
        "qa_gate": gate,
        "planning_lint": planning_lint_report,
        "planning_lint_targeted": planning_lint_targeted,
        "tool": {
            "argv": sys.argv,
            "cwd": os.getcwd(),
            "python": sys.version.split()[0],
        },
    }
    return pack


def _write_pack(pack: dict[str, Any], *, out_dir: Path, label: str, write_latest: bool) -> tuple[Path, Path, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = _utc_now_compact()
    json_path = out_dir / f"production_pack_{label}__{ts}.json"
    md_path = out_dir / f"production_pack_{label}__{ts}.md"
    json_path.write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    gate = pack.get("qa_gate") if isinstance(pack, dict) else None
    title = ""
    try:
        pr = pack.get("planning", {}).get("row")  # type: ignore[union-attr]
        if isinstance(pr, dict):
            title = (pr.get("タイトル") or "").strip()
    except Exception:
        title = ""

    lines: list[str] = []
    lines.append(f"# production_pack report: {label}")
    lines.append("")
    lines.append(f"- generated_at: {pack.get('generated_at')}")
    lines.append(f"- channel/video: {pack.get('channel')}/{pack.get('video')}")
    lines.append(f"- script_id: {pack.get('script_id')}")
    lines.append(f"- qa_gate: {gate.get('result') if isinstance(gate, dict) else 'unknown'}")
    if isinstance(gate, dict) and gate.get("score") is not None:
        lines.append(f"- qa_score: {gate.get('score')}")
    lines.append("- remediation: ssot/ops/OPS_PREPRODUCTION_REMEDIATION.md")
    if title:
        lines.append(f"- title: {title}")
    lines.append("")
    lines.append("## QA issues")
    if isinstance(gate, dict) and isinstance(gate.get("issues"), list) and gate["issues"]:
        for it in gate["issues"]:
            if not isinstance(it, dict):
                continue
            lines.append(f"- [{it.get('severity')}] {it.get('code')}: {it.get('message')}")
    else:
        lines.append("- (none)")
    md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    if write_latest:
        latest_json = out_dir / f"production_pack_{label}__latest.json"
        latest_md = out_dir / f"production_pack_{label}__latest.md"
        latest_json.write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        latest_md.write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Wrote: {latest_json}")
        print(f"Wrote: {latest_md}")

    return json_path, md_path, ts


def _write_diff(
    payload: dict[str, Any], *, out_dir: Path, label: str, ts: str, write_latest: bool
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"production_pack_{label}__diff__{ts}.json"
    md_path = out_dir / f"production_pack_{label}__diff__{ts}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    changes = payload.get("changes")
    lines: list[str] = []
    lines.append(f"# production_pack diff: {label}")
    lines.append("")
    lines.append(f"- generated_at: {payload.get('generated_at')}")
    lines.append(f"- previous_exists: {payload.get('previous_exists')}")
    lines.append(f"- changes: {len(changes) if isinstance(changes, list) else 0}")
    lines.append("")
    lines.append("## Changes (first 120)")
    if isinstance(changes, list) and changes:
        for it in changes[:120]:
            if not isinstance(it, dict):
                continue
            p = it.get("path")
            typ = it.get("type")
            before = json.dumps(it.get("before"), ensure_ascii=False)
            after = json.dumps(it.get("after"), ensure_ascii=False)
            if len(before) > 140:
                before = before[:140] + "…"
            if len(after) > 140:
                after = after[:140] + "…"
            lines.append(f"- {typ} {p}: {before} -> {after}")
    else:
        lines.append("- (none)")

    md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    if write_latest:
        (out_dir / f"production_pack_{label}__diff__latest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (out_dir / f"production_pack_{label}__diff__latest.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    return json_path, md_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True, help="Channel code like CH01")
    ap.add_argument("--video", required=True, help="Video number like 001 (or 1)")
    ap.add_argument("--write-latest", action="store_true", help="Also write *_latest.json/md (overwrite)")
    ap.add_argument("--stdout", action="store_true", help="Also print the JSON to stdout")
    args = ap.parse_args()

    channel = _normalize_channel(str(args.channel))
    video = _normalize_video(str(args.video))
    label = f"{channel}_{video}"

    out_dir = logs_root() / "regression" / "production_pack"
    prev_latest: dict[str, Any] | None = None
    prev_latest_path = out_dir / f"production_pack_{label}__latest.json"
    prev_latest_error: str | None = None
    if args.write_latest and prev_latest_path.exists():
        try:
            prev_latest = json.loads(prev_latest_path.read_text(encoding="utf-8"))
        except Exception as e:
            prev_latest_error = repr(e)

    pack = _build_pack(channel=channel, video=video)
    json_path, md_path, ts = _write_pack(pack, out_dir=out_dir, label=label, write_latest=bool(args.write_latest))
    print(f"Wrote: {json_path}")
    print(f"Wrote: {md_path}")

    if args.write_latest:
        ignore_paths = {"generated_at", "tool.argv", "tool.cwd", "tool.python"}
        changes = _diff_packs(prev_latest or {}, pack, ignore_paths=ignore_paths)
        diff_payload: dict[str, Any] = {
            "schema": "ytm.production_pack_diff.v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "label": label,
            "previous_exists": prev_latest is not None,
            "previous_path": str(prev_latest_path),
            "previous_load_error": prev_latest_error,
            "current_path": str(json_path),
            "ignored_paths": sorted(ignore_paths),
            "changes": changes,
        }
        diff_json, diff_md = _write_diff(diff_payload, out_dir=out_dir, label=label, ts=ts, write_latest=True)
        print(f"Wrote: {diff_json}")
        print(f"Wrote: {diff_md}")

    if args.stdout:
        print(json.dumps(pack, ensure_ascii=False, indent=2))

    gate = pack.get("qa_gate")
    if isinstance(gate, dict) and gate.get("result") == "fail":
        return 2
    if isinstance(gate, dict) and gate.get("result") == "warn":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

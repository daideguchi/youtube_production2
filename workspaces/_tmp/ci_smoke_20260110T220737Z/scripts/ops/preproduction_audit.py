#!/usr/bin/env python3
"""
Pre-production audit: enumerate missing/weak spots before mass production.

Goals:
- Make "抜け漏れ" visible as a deterministic report (no LLM).
- Keep the current pipeline intact (audit does not mutate SoT).
- Provide a single snapshot log under workspaces/logs/regression/preproduction_audit/.

Usage:
  python3 scripts/ops/preproduction_audit.py --all --write-latest
  python3 scripts/ops/preproduction_audit.py --channel CH02 --write-latest
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from _bootstrap import bootstrap

PROJECT_ROOT = bootstrap(load_env=False)

from factory_common.paths import logs_root, repo_root, script_pkg_root, video_pkg_root
from preproduction_issue_catalog import fix_hints_for_issue


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


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


def _resolve_repo_path(value: str) -> Path:
    p = Path(str(value or ""))
    if p.is_absolute():
        return p
    return repo_root() / str(value)


def _resolve_video_pkg_path(value: str) -> Path:
    """
    Mirror video_pipeline behavior:
      - "templates/foo.txt" -> video_pkg_root()/templates/foo.txt
      - "foo.txt" -> video_pkg_root()/templates/foo.txt
    """
    raw = str(value or "").strip()
    p = Path(raw)
    if p.is_absolute():
        return p
    if p.name == raw:
        return video_pkg_root() / "templates" / p.name
    return video_pkg_root() / raw


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


def _load_sources_doc() -> dict[str, Any]:
    """
    Mirror runner behavior:
      - primary: repo-root `configs/sources.yaml`
      - overlay: `packages/script_pipeline/config/sources.yaml`
    """
    global_path = repo_root() / "configs" / "sources.yaml"
    local_path = script_pkg_root() / "config" / "sources.yaml"
    base = _load_yaml_optional(global_path)
    overlay = _load_yaml_optional(local_path)
    return _deep_merge_dict(base, overlay)


def _normalize_channel(ch: str) -> str:
    s = (ch or "").strip().upper()
    if re.fullmatch(r"CH\\d{2}", s):
        return s
    m = re.fullmatch(r"CH(\\d+)", s)
    if m:
        return f"CH{int(m.group(1)):02d}"
    return s


def _iter_channel_dirs() -> list[Path]:
    root = script_pkg_root() / "channels"
    if not root.exists():
        return []
    return sorted([p for p in root.glob("CH??-*") if p.is_dir()])


def _add_issue(
    issues: list[dict[str, Any]],
    *,
    severity: str,
    code: str,
    message: str,
    channel: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    it: dict[str, Any] = {"severity": severity, "code": code, "message": message}
    if channel:
        it["channel"] = channel
    if details:
        it["details"] = details
    hints = fix_hints_for_issue(code, channel=channel)
    if hints:
        it["fix_hints"] = hints
    issues.append(it)


def _finalize(issues: list[dict[str, Any]]) -> dict[str, Any]:
    errors = sum(1 for it in issues if isinstance(it, dict) and it.get("severity") == "error")
    warnings = sum(1 for it in issues if isinstance(it, dict) and it.get("severity") == "warning")
    if errors > 0:
        result = "fail"
        score = 0
    elif warnings > 0:
        result = "warn"
        score = max(0, 100 - warnings * 3)
    else:
        result = "pass"
        score = 100
    return {"result": result, "score": score, "counts": {"errors": errors, "warnings": warnings}, "issues": issues}


def _write_report(payload: dict[str, Any], *, out_dir: Path, label: str, write_latest: bool) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = _utc_now_compact()
    json_path = out_dir / f"preproduction_audit_{label}__{ts}.json"
    md_path = out_dir / f"preproduction_audit_{label}__{ts}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    gate = payload.get("gate") if isinstance(payload, dict) else None
    lines: list[str] = []
    lines.append(f"# preproduction_audit report: {label}")
    lines.append("")
    lines.append(f"- generated_at: {payload.get('generated_at')}")
    lines.append(f"- channels_checked: {payload.get('channels_checked')}")
    lines.append(f"- gate: {gate.get('result') if isinstance(gate, dict) else 'unknown'}")
    if isinstance(gate, dict) and gate.get("score") is not None:
        lines.append(f"- score: {gate.get('score')}")
    lines.append("- remediation: ssot/ops/OPS_PREPRODUCTION_REMEDIATION.md")
    lines.append("")
    lines.append("## Issues (first 120)")
    issues = gate.get("issues") if isinstance(gate, dict) else None
    if isinstance(issues, list) and issues:
        for it in issues[:120]:
            if not isinstance(it, dict):
                continue
            ch = str(it.get("channel") or "").strip()
            prefix = f"{ch} " if ch else ""
            lines.append(f"- [{it.get('severity')}] {prefix}{it.get('code')}: {it.get('message')}")
    else:
        lines.append("- (none)")
    md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    if write_latest:
        (out_dir / f"preproduction_audit_{label}__latest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (out_dir / f"preproduction_audit_{label}__latest.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    return json_path, md_path


def _audit_channel(
    channel_dir: Path,
    *,
    sources_channels: dict[str, Any],
    presets_channels: dict[str, Any],
    registry_template_ids: set[str] | None,
    include_planning_lint: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    channel_code = _normalize_channel(channel_dir.name.split("-", 1)[0])

    # sources.yaml (merged) presence/schema
    ch_sources = sources_channels.get(channel_code)
    if not isinstance(ch_sources, dict):
        _add_issue(
            issues,
            severity="error",
            code="missing_sources_entry",
            message=f"configs/sources.yaml missing channels.{channel_code}",
            channel=channel_code,
        )
        ch_sources = {}

    planning_csv_val = str(ch_sources.get("planning_csv") or "")
    persona_val = str(ch_sources.get("persona") or "")
    channel_prompt_val = str(ch_sources.get("channel_prompt") or "")

    planning_csv_path = _resolve_repo_path(planning_csv_val) if planning_csv_val else None
    persona_path = _resolve_repo_path(persona_val) if persona_val else None
    channel_prompt_path = _resolve_repo_path(channel_prompt_val) if channel_prompt_val else None

    if not planning_csv_val:
        _add_issue(
            issues,
            severity="error",
            code="missing_sources_planning_csv",
            message=f"sources.yaml missing planning_csv for {channel_code}",
            channel=channel_code,
        )
    if not persona_val:
        _add_issue(
            issues,
            severity="warning",
            code="missing_sources_persona",
            message=f"sources.yaml missing persona for {channel_code}",
            channel=channel_code,
        )
    if not channel_prompt_val:
        _add_issue(
            issues,
            severity="error",
            code="missing_sources_channel_prompt",
            message=f"sources.yaml missing channel_prompt for {channel_code}",
            channel=channel_code,
        )

    # channel files
    info_path = channel_dir / "channel_info.json"
    prompt_path = channel_dir / "script_prompt.txt"

    channel_info: dict[str, Any] | None = None
    if not info_path.exists():
        _add_issue(
            issues,
            severity="error",
            code="missing_channel_info_json",
            message=f"Missing: {info_path}",
            channel=channel_code,
        )
    else:
        try:
            channel_info_raw = json.loads(info_path.read_text(encoding="utf-8"))
            channel_info = channel_info_raw if isinstance(channel_info_raw, dict) else None
        except Exception as e:
            _add_issue(
                issues,
                severity="error",
                code="invalid_channel_info_json",
                message=f"Invalid JSON: {info_path}: {e!r}",
                channel=channel_code,
            )

    if not prompt_path.exists():
        _add_issue(
            issues,
            severity="warning",
            code="missing_script_prompt_txt",
            message=f"Missing: {prompt_path}",
            channel=channel_code,
        )

    video_workflow = str((channel_info or {}).get("video_workflow") or "").strip().lower()
    if not video_workflow:
        _add_issue(
            issues,
            severity="warning",
            code="missing_video_workflow",
            message=f"channel_info.json missing video_workflow: {channel_code}",
            channel=channel_code,
        )

    # File existence checks from sources.yaml (runner resolves repo-relative paths).
    planning_csv_meta = _file_meta(planning_csv_path) if planning_csv_path else None
    persona_meta = _file_meta(persona_path) if persona_path else None
    channel_prompt_meta = _file_meta(channel_prompt_path) if channel_prompt_path else None

    if planning_csv_path and not planning_csv_path.exists():
        _add_issue(
            issues,
            severity="error",
            code="missing_planning_csv",
            message=f"Missing: {planning_csv_path}",
            channel=channel_code,
        )
    if persona_path and not persona_path.exists():
        _add_issue(
            issues,
            severity="warning",
            code="missing_persona",
            message=f"Missing: {persona_path}",
            channel=channel_code,
        )
    if channel_prompt_path and not channel_prompt_path.exists():
        _add_issue(
            issues,
            severity="error",
            code="missing_channel_prompt",
            message=f"Missing: {channel_prompt_path}",
            channel=channel_code,
        )

    voice_config_path = script_pkg_root() / "audio" / "channels" / channel_code / "voice_config.json"
    voice_config_meta = _file_meta(voice_config_path)
    if not voice_config_path.exists():
        _add_issue(
            issues,
            severity="error",
            code="missing_voice_config",
            message=f"Missing: {voice_config_path}",
            channel=channel_code,
        )
    else:
        try:
            raw = json.loads(voice_config_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                _add_issue(
                    issues,
                    severity="error",
                    code="invalid_voice_config_schema",
                    message=f"voice_config.json must be an object: {voice_config_path}",
                    channel=channel_code,
                )
        except Exception as e:
            _add_issue(
                issues,
                severity="error",
                code="invalid_voice_config_json",
                message=f"Invalid JSON: {voice_config_path}: {e!r}",
                channel=channel_code,
            )

    # CapCut preset is required only for capcut workflow channels.
    preset = presets_channels.get(channel_code) if isinstance(presets_channels, dict) else None
    prompt_template_path: Path | None = None
    prompt_template_registered: bool | None = None
    if video_workflow == "capcut":
        if not isinstance(preset, dict):
            _add_issue(
                issues,
                severity="error",
                code="missing_video_channel_preset",
                message=f"channel_presets.json missing channels.{channel_code} (required for capcut workflow)",
                channel=channel_code,
            )
        else:
            status = str(preset.get("status") or "active").strip().lower()
            if status == "active":
                tpl = str(preset.get("capcut_template") or "").strip()
                if not tpl:
                    _add_issue(
                        issues,
                        severity="error",
                        code="active_preset_missing_capcut_template",
                        message=f"active preset missing capcut_template: {channel_code}",
                        channel=channel_code,
                    )
                prompt_tpl = str(preset.get("prompt_template") or "").strip()
                if not prompt_tpl:
                    _add_issue(
                        issues,
                        severity="warning",
                        code="active_preset_missing_prompt_template",
                        message=f"active preset missing prompt_template: {channel_code}",
                        channel=channel_code,
                    )
                else:
                    prompt_template_path = _resolve_video_pkg_path(prompt_tpl)
                    if not prompt_template_path.exists():
                        _add_issue(
                            issues,
                            severity="error",
                            code="missing_prompt_template_file",
                            message=f"prompt_template file not found: {prompt_template_path}",
                            channel=channel_code,
                        )
                    prompt_template_id = Path(prompt_tpl).name
                    if registry_template_ids is None:
                        prompt_template_registered = None
                    else:
                        prompt_template_registered = prompt_template_id in registry_template_ids
                        if not prompt_template_registered:
                            _add_issue(
                                issues,
                                severity="warning",
                                code="prompt_template_not_registered",
                                message=f"prompt_template not registered in template_registry.json: {prompt_template_id}",
                                channel=channel_code,
                            )
            if preset.get("persona_required") and (persona_path is None or not persona_path.exists()):
                _add_issue(
                    issues,
                    severity="error",
                    code="persona_required_but_missing",
                    message=f"persona_required=true but persona is missing: {channel_code}",
                    channel=channel_code,
                )

    # Benchmarks are optional but strongly recommended (warn only).
    benchmarks_summary: dict[str, Any] | None = None
    b = (channel_info or {}).get("benchmarks")
    if not isinstance(b, dict):
        _add_issue(
            issues,
            severity="warning",
            code="missing_benchmarks",
            message=f"channel_info.json missing benchmarks: {channel_code}",
            channel=channel_code,
        )
    else:
        allow_empty = bool(b.get("allow_empty_channels", False))
        channels_list = b.get("channels") if isinstance(b.get("channels"), list) else []
        samples_list = b.get("script_samples") if isinstance(b.get("script_samples"), list) else []
        if not allow_empty and not channels_list:
            _add_issue(
                issues,
                severity="warning",
                code="benchmarks_empty_channels",
                message=f"benchmarks.channels is empty: {channel_code}",
                channel=channel_code,
            )
        if not samples_list:
            _add_issue(
                issues,
                severity="warning",
                code="benchmarks_empty_script_samples",
                message=f"benchmarks.script_samples is empty: {channel_code}",
                channel=channel_code,
            )
        benchmarks_summary = {
            "updated_at": b.get("updated_at"),
            "channels_count": len(channels_list) if isinstance(channels_list, list) else None,
            "script_samples_count": len(samples_list) if isinstance(samples_list, list) else None,
        }

    planning_lint_summary: dict[str, Any] | None = None
    if include_planning_lint and planning_csv_path and planning_csv_path.exists():
        try:
            import planning_lint as _planning_lint

            rep = _planning_lint.lint_planning_csv(planning_csv_path, channel_code)
            counts = rep.get("counts") if isinstance(rep, dict) else None
            ok = bool(rep.get("ok")) if isinstance(rep, dict) else False
            planning_lint_summary = {"ok": ok, "counts": counts}
            if not ok:
                _add_issue(
                    issues,
                    severity="error",
                    code="planning_lint_failed",
                    message=f"planning_lint failed for {channel_code}",
                    channel=channel_code,
                    details={"counts": counts},
                )
            else:
                by_sev = (counts or {}).get("by_severity") if isinstance(counts, dict) else {}
                if isinstance(by_sev, dict) and int(by_sev.get("warning", 0) or 0) > 0:
                    _add_issue(
                        issues,
                        severity="warning",
                        code="planning_lint_warnings",
                        message=f"planning_lint has warnings for {channel_code}",
                        channel=channel_code,
                        details={"counts": counts},
                    )
        except Exception as e:
            _add_issue(
                issues,
                severity="warning",
                code="planning_lint_exception",
                message=f"{channel_code}: {e!r}",
                channel=channel_code,
            )

    ch_gate = _finalize(issues)
    ch_gate_summary = {k: v for k, v in ch_gate.items() if k != "issues"}

    channel_payload: dict[str, Any] = {
        "channel": channel_code,
        "channel_dir": str(channel_dir),
        "channel_info_json": _file_meta(info_path),
        "script_prompt_txt": _file_meta(prompt_path),
        "video_workflow": video_workflow or None,
        "gate": ch_gate_summary,
        "issues_sample": issues[:25],
        "sources": {
            "planning_csv": planning_csv_val or None,
            "persona": persona_val or None,
            "channel_prompt": channel_prompt_val or None,
            "chapter_count": ch_sources.get("chapter_count"),
            "target_chars_min": ch_sources.get("target_chars_min"),
            "target_chars_max": ch_sources.get("target_chars_max"),
            "web_search_policy": ch_sources.get("web_search_policy"),
        },
        "resolved": {
            "planning_csv": planning_csv_meta,
            "persona": persona_meta,
            "channel_prompt": channel_prompt_meta,
            "video_channel_preset": preset,
            "voice_config": voice_config_meta,
            "prompt_template": _file_meta(prompt_template_path) if prompt_template_path else None,
            "prompt_template_registered": prompt_template_registered,
        },
        "benchmarks_summary": benchmarks_summary,
        "planning_lint_summary": planning_lint_summary,
    }
    return channel_payload, issues


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", action="append", help="Audit only these channels (repeatable)")
    ap.add_argument("--all", action="store_true", help="Audit all channels (default when --channel not provided)")
    ap.add_argument("--no-planning-lint", action="store_true", help="Skip embedded planning_lint checks")
    ap.add_argument("--write-latest", action="store_true", help="Also write *_latest.json/md (overwrite)")
    ap.add_argument("--stdout", action="store_true", help="Also print JSON to stdout")
    args = ap.parse_args(argv)

    selected = [_normalize_channel(s) for s in (args.channel or []) if str(s).strip()]
    if not selected:
        args.all = True

    sources_doc = _load_sources_doc()
    sources_channels = sources_doc.get("channels") if isinstance(sources_doc, dict) else {}
    sources_channels = sources_channels if isinstance(sources_channels, dict) else {}

    presets_path = video_pkg_root() / "config" / "channel_presets.json"
    presets_doc = _load_yaml_optional(presets_path) if presets_path.suffix.lower() in {".yaml", ".yml"} else {}
    # channel_presets.json is JSON, but yaml.safe_load can parse JSON too; keep simple.
    if not presets_doc and presets_path.exists():
        try:
            presets_doc = json.loads(presets_path.read_text(encoding="utf-8"))
        except Exception:
            presets_doc = {}
    presets_channels = presets_doc.get("channels") if isinstance(presets_doc, dict) else {}
    presets_channels = presets_channels if isinstance(presets_channels, dict) else {}

    registry_path = video_pkg_root() / "config" / "template_registry.json"
    registry_template_ids: set[str] | None = set()
    try:
        registry_doc = json.loads(registry_path.read_text(encoding="utf-8"))
        templates = registry_doc.get("templates") if isinstance(registry_doc, dict) else None
        if isinstance(templates, list):
            for t in templates:
                if isinstance(t, dict) and str(t.get("id") or "").strip():
                    registry_template_ids.add(Path(str(t.get("id") or "")).name)
    except Exception:
        registry_template_ids = None

    issues: list[dict[str, Any]] = []
    channel_results: list[dict[str, Any]] = []

    channel_dirs = _iter_channel_dirs()
    for d in channel_dirs:
        ch = _normalize_channel(d.name.split("-", 1)[0])
        if selected and ch not in selected:
            continue
        rep, ch_issues = _audit_channel(
            d,
            sources_channels=sources_channels,
            presets_channels=presets_channels,
            registry_template_ids=registry_template_ids,
            include_planning_lint=not bool(args.no_planning_lint),
        )
        channel_results.append(rep)
        issues.extend(ch_issues)

    gate = _finalize(issues)
    by_code = Counter(str(it.get("code") or "") for it in issues if isinstance(it, dict))
    by_severity = Counter(str(it.get("severity") or "") for it in issues if isinstance(it, dict))

    payload: dict[str, Any] = {
        "schema": "ytm.preproduction_audit.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "channels_checked": len(channel_results),
        "gate": gate,
        "counts": {"by_severity": dict(by_severity), "by_code": dict(by_code)},
        "sources": {
            "global_sources_yaml": _file_meta(repo_root() / "configs" / "sources.yaml"),
            "local_sources_yaml": _file_meta(script_pkg_root() / "config" / "sources.yaml"),
        },
        "video": {
            "channel_presets_json": _file_meta(presets_path),
            "template_registry_json": _file_meta(registry_path),
        },
        "channels": channel_results,
        "tool": {"argv": sys.argv, "cwd": os.getcwd(), "python": sys.version.split()[0]},
    }

    out_dir = logs_root() / "regression" / "preproduction_audit"
    label = "all" if not selected else "_".join(selected)
    json_path, md_path = _write_report(payload, out_dir=out_dir, label=label, write_latest=bool(args.write_latest))
    print(f"Wrote: {json_path}")
    print(f"Wrote: {md_path}")

    if args.stdout:
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    if gate.get("result") == "fail":
        return 2
    if gate.get("result") == "warn":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

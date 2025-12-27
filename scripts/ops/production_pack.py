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
    repo_root,
    script_pkg_root,
    thumbnails_root,
    video_pkg_root,
)


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
) -> None:
    it: dict[str, Any] = {"severity": severity, "code": code, "message": message}
    if details:
        it["details"] = details
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
    script_channels_json = repo_root() / "packages" / "script_pipeline" / "channels" / "channels.json"
    script_templates = script_pkg_root() / "templates.yaml"
    script_stages = script_pkg_root() / "stages.yaml"
    video_channel_presets = video_pkg_root() / "config" / "channel_presets.json"
    video_templates_registry = video_pkg_root() / "config" / "template_registry.json"
    image_system_prompt = video_pkg_root() / "system_prompt_for_image_generation.txt"
    thumbnail_templates = thumbnails_root() / "templates.json"

    gate_issues: list[dict[str, Any]] = []

    if not csv_path.exists():
        _add_gate_issue(
            gate_issues,
            severity="error",
            code="missing_planning_csv",
            message=f"CSV not found: {csv_path}",
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
            )

        title = ""
        if isinstance(planning.get("row"), dict):
            title = (planning["row"].get("タイトル") or "").strip()
        if not title and planning["ok"]:
            _add_gate_issue(gate_issues, severity="error", code="missing_title", message="Planning タイトル is empty")

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
                )

            # If planning_lint found errors elsewhere, warn but do not fail this episode.
            if global_errors > 0 and not any(it.get("severity") == "error" for it in targeted):
                _add_gate_issue(
                    gate_issues,
                    severity="warning",
                    code="planning_lint_global_errors",
                    message=f"planning_lint found errors in other rows: {global_errors} errors, {global_warnings} warnings",
                )
        except Exception as e:
            # Lint failure should not block pack creation, but it should be visible.
            _add_gate_issue(gate_issues, severity="warning", code="planning_lint_exception", message=repr(e))
            planning_lint_targeted = None

    if not persona.exists():
        _add_gate_issue(gate_issues, severity="warning", code="missing_persona", message=f"Persona not found: {persona}")

    ch_dir = _find_script_channel_dir(channel)
    script_prompt_path: Path | None = None
    if not ch_dir.get("ok", False):
        _add_gate_issue(
            gate_issues,
            severity="error",
            code="missing_script_channel_dir",
            message=f"Channel dir not found for {channel}",
        )
    else:
        script_prompt_path = Path(str(ch_dir["path"])) / "script_prompt.txt"
        if not script_prompt_path.exists():
            _add_gate_issue(
                gate_issues,
                severity="warning",
                code="missing_script_prompt",
                message=f"Missing: {script_prompt_path}",
            )

    planning_patches = _planning_patches_for_target(channel, video)

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
            "row": planning.get("row"),
        },
        "planning_patches": planning_patches,
        "resolved": {
            "script_pipeline": {
                "channels_json": _file_meta(script_channels_json),
                "channel_dir": ch_dir,
                "script_prompt": _file_meta(script_prompt_path) if script_prompt_path else None,
                "templates_yaml": _file_meta(script_templates),
                "stages_yaml": _file_meta(script_stages),
            },
            "video_pipeline": {
                "channel_presets_json": _file_meta(video_channel_presets),
                "template_registry_json": _file_meta(video_templates_registry),
                "image_system_prompt": _file_meta(image_system_prompt),
            },
            "thumbnails": {
                "templates_json": _file_meta(thumbnail_templates),
            },
        },
        "optional_inputs": {
            "persona": _file_meta(persona),
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

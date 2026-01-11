#!/usr/bin/env python3
"""
Apply a small, auditable "planning patch" onto a Planning CSV.

Why:
- Handle "企画の上書き/部分更新" without ad-hoc CSV edits.
- Leave a diff log under workspaces/logs/regression/planning_patch/.
- Respect multi-agent coordination locks by default.

Patch format SSOT:
  ssot/ops/OPS_PLANNING_PATCHES.md

Usage:
  python3 scripts/ops/planning_apply_patch.py --patch workspaces/planning/patches/CH02-024__retitle.yaml
  python3 scripts/ops/planning_apply_patch.py --patch ... --apply
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import yaml

from _bootstrap import bootstrap

PROJECT_ROOT = bootstrap(load_env=False)

from factory_common.locks import default_active_locks_for_mutation, find_blocking_lock
from factory_common.paths import channels_csv_path, logs_root, repo_root


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


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _file_meta(path: Path) -> dict[str, Any]:
    try:
        st = path.stat()
        return {
            "path": str(path),
            "exists": True,
            "size_bytes": st.st_size,
            "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
            "sha256": _sha256_file(path),
        }
    except Exception:
        return {"path": str(path), "exists": False}


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return (list(reader.fieldnames or []), list(reader))


def _write_csv(path: Path, headers: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


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


def _find_row_index(rows: list[dict[str, str]], *, channel: str, video: str) -> int | None:
    target_sid = f"{channel}-{video}"
    for idx, row in enumerate(rows):
        row_video = _normalize_video(_video_number_from_row(row))
        row_sid = _script_id_from_row(row)
        if row_video == video or row_sid == target_sid:
            return idx
    return None


@dataclass(frozen=True)
class PatchSpec:
    patch_id: str
    channel: str
    video: str
    op: str  # "set" | "add_row"
    values: dict[str, str]
    notes: str


def _load_patch(path: Path) -> tuple[PatchSpec | None, list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    raw_bytes = path.read_bytes()
    meta = {"path": str(path), "sha256": _sha256_bytes(raw_bytes)}

    try:
        data = yaml.safe_load(raw_bytes.decode("utf-8"))
    except Exception as e:
        issues.append({"severity": "error", "code": "invalid_yaml", "message": f"{meta['path']}: {e!r}"})
        return None, issues

    if not isinstance(data, dict):
        issues.append({"severity": "error", "code": "invalid_patch", "message": f"{meta['path']}: root must be a mapping"})
        return None, issues

    schema = str(data.get("schema") or "").strip()
    if schema != "ytm.planning_patch.v1":
        issues.append(
            {
                "severity": "error",
                "code": "unexpected_schema",
                "message": f"{meta['path']}: expected schema=ytm.planning_patch.v1, got {schema!r}",
            }
        )
        return None, issues

    patch_id = str(data.get("patch_id") or "").strip() or path.stem
    target = data.get("target")
    if not isinstance(target, dict):
        issues.append({"severity": "error", "code": "missing_target", "message": f"{meta['path']}: target is required"})
        return None, issues

    channel = _normalize_channel(str(target.get("channel") or ""))
    video = _normalize_video(str(target.get("video") or ""))
    if not channel or not re.fullmatch(r"CH\d{2}", channel):
        issues.append({"severity": "error", "code": "invalid_channel", "message": f"{meta['path']}: invalid channel={channel!r}"})
        return None, issues
    if not video or not re.fullmatch(r"\d{3}", video):
        issues.append({"severity": "error", "code": "invalid_video", "message": f"{meta['path']}: invalid video={video!r}"})
        return None, issues

    apply = data.get("apply")
    if not isinstance(apply, dict):
        issues.append({"severity": "error", "code": "missing_apply", "message": f"{meta['path']}: apply is required"})
        return None, issues

    set_values_raw = apply.get("set")
    add_row_raw = apply.get("add_row")
    if set_values_raw is not None and add_row_raw is not None:
        issues.append(
            {"severity": "error", "code": "multiple_apply_ops", "message": f"{meta['path']}: use exactly one of apply.set or apply.add_row"}
        )
        return None, issues

    op = ""
    values_raw: dict[str, Any] | None = None
    if set_values_raw is not None:
        op = "set"
        values_raw = set_values_raw if isinstance(set_values_raw, dict) else None
    elif add_row_raw is not None:
        op = "add_row"
        values_raw = add_row_raw if isinstance(add_row_raw, dict) else None
    else:
        issues.append({"severity": "error", "code": "missing_apply_op", "message": f"{meta['path']}: apply.set or apply.add_row is required"})
        return None, issues

    if not isinstance(values_raw, dict) or not values_raw:
        issues.append({"severity": "error", "code": "empty_apply", "message": f"{meta['path']}: apply.{op} must be a non-empty mapping"})
        return None, issues

    values: dict[str, str] = {}
    for k, v in values_raw.items():
        key = str(k or "").strip()
        if not key:
            continue
        values[key] = "" if v is None else str(v)

    if not values:
        issues.append({"severity": "error", "code": "empty_apply", "message": f"{meta['path']}: apply.{op} is empty"})
        return None, issues

    notes = str(data.get("notes") or "").rstrip()
    return PatchSpec(patch_id=patch_id, channel=channel, video=video, op=op, values=values, notes=notes), issues


def _write_report(payload: dict[str, Any], *, out_dir: Path, label: str, write_latest: bool) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = _utc_now_compact()
    json_path = out_dir / f"planning_patch_{label}__{ts}.json"
    md_path = out_dir / f"planning_patch_{label}__{ts}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines: list[str] = []
    lines.append(f"# planning_patch report: {label}")
    lines.append("")
    lines.append(f"- generated_at: {payload.get('generated_at')}")
    lines.append(f"- ok: {payload.get('ok')}")
    lines.append(f"- mode: {payload.get('mode')}")
    lines.append(f"- target: {payload.get('target')}")
    lines.append(f"- csv_path: {payload.get('csv_path')}")
    lines.append("")
    lines.append("## Changes")
    changes = payload.get("changes")
    if isinstance(changes, list) and changes:
        for c in changes[:80]:
            if not isinstance(c, dict):
                continue
            before = str(c.get("before") or "").replace("\n", "\\n")
            after = str(c.get("after") or "").replace("\n", "\\n")
            if len(before) > 90:
                before = before[:90] + "…"
            if len(after) > 90:
                after = after[:90] + "…"
            lines.append(f"- {c.get('column')}: '{before}' -> '{after}'")
    else:
        lines.append("- (none)")
    md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    if write_latest:
        (out_dir / f"planning_patch_{label}__latest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (out_dir / f"planning_patch_{label}__latest.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    return json_path, md_path


def _safe_slug(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return "patch"
    # Keep filenames conservative; avoid spaces and odd chars.
    s = re.sub(r"[^0-9A-Za-z._-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "patch"


def _apply_patch_to_csv(
    patch: PatchSpec,
    *,
    patch_path: Path,
    csv_path: Path,
    apply: bool,
    allow_new_columns: bool,
    ignore_locks: bool,
    write_latest: bool,
) -> tuple[dict[str, Any], int]:
    out_dir = logs_root() / "regression" / "planning_patch"
    label = f"{patch.channel}_{patch.video}__{_safe_slug(patch.patch_id)}"

    def _write_and_return(payload: dict[str, Any], code: int) -> tuple[dict[str, Any], int]:
        report_json, report_md = _write_report(payload, out_dir=out_dir, label=label, write_latest=write_latest)
        print(f"Wrote: {report_json}")
        print(f"Wrote: {report_md}")
        return payload, code

    issues: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    before_row: dict[str, str] = {}
    row_after: dict[str, str] | None = None
    row_index_1based: int | None = None

    candidate_csv_path: Path | None = None
    candidate_csv_meta: dict[str, Any] | None = None
    candidate_lint: dict[str, Any] | None = None
    candidate_csv_deleted_after_apply = False

    applied = False
    backup_meta: dict[str, Any] | None = None
    post_apply_lint: dict[str, Any] | None = None
    lint_exception: str | None = None

    if not csv_path.exists():
        return _write_and_return(
            {
                "schema": "ytm.planning_patch_apply.v1",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "ok": False,
                "mode": "apply" if apply else "dry-run",
                "patch_id": patch.patch_id,
                "target": {"channel": patch.channel, "video": patch.video},
                "csv_path": str(csv_path),
                "patch_file": _file_meta(patch_path),
                "issues": [{"severity": "error", "code": "missing_csv", "message": f"CSV not found: {csv_path}"}],
                "changes": [],
            },
            2,
        )

    if apply and not ignore_locks:
        locks = default_active_locks_for_mutation()
        blocking = find_blocking_lock(csv_path, locks)
        if blocking is not None:
            return _write_and_return(
                {
                    "schema": "ytm.planning_patch_apply.v1",
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "ok": False,
                    "mode": "apply",
                    "patch_id": patch.patch_id,
                    "target": {"channel": patch.channel, "video": patch.video},
                    "csv_path": str(csv_path),
                    "patch_file": _file_meta(patch_path),
                    "issues": [
                        {
                            "severity": "error",
                            "code": "blocked_by_lock",
                            "message": f"blocked by active lock: {blocking.lock_id} mode={blocking.mode} created_by={blocking.created_by}",
                            "scopes": list(blocking.scopes),
                        }
                    ],
                    "changes": [],
                },
                2,
            )

    headers, rows = _read_csv(csv_path)
    row_idx = _find_row_index(rows, channel=patch.channel, video=patch.video)

    if patch.op == "set":
        if row_idx is None:
            return _write_and_return(
                {
                    "schema": "ytm.planning_patch_apply.v1",
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "ok": False,
                    "mode": "apply" if apply else "dry-run",
                    "patch_id": patch.patch_id,
                    "target": {"channel": patch.channel, "video": patch.video},
                    "csv_path": str(csv_path),
                    "patch_file": _file_meta(patch_path),
                    "issues": [{"severity": "error", "code": "missing_row", "message": f"No row for {patch.channel}/{patch.video}"}],
                    "changes": [],
                },
                2,
            )

        before_row = dict(rows[row_idx])
        for col, value in patch.values.items():
            if col not in headers:
                if allow_new_columns:
                    headers.append(col)
                else:
                    issues.append(
                        {
                            "severity": "error",
                            "code": "unknown_column",
                            "message": f"Column not found in CSV header: {col!r} (use --allow-new-columns to append)",
                        }
                    )
                    continue

            old = str(rows[row_idx].get(col) or "")
            new = str(value or "")
            if old == new:
                continue
            rows[row_idx][col] = new
            changes.append({"column": col, "before": old, "after": new})

        title_after = (rows[row_idx].get("タイトル") or "").strip() if isinstance(rows[row_idx], dict) else ""
        if not title_after:
            issues.append({"severity": "error", "code": "missing_title_after", "message": "タイトル would become empty"})

        row_after = dict(rows[row_idx])
        row_index_1based = row_idx + 1

    elif patch.op == "add_row":
        if row_idx is not None:
            return _write_and_return(
                {
                    "schema": "ytm.planning_patch_apply.v1",
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "ok": False,
                    "mode": "apply" if apply else "dry-run",
                    "patch_id": patch.patch_id,
                    "target": {"channel": patch.channel, "video": patch.video},
                    "csv_path": str(csv_path),
                    "patch_file": _file_meta(patch_path),
                    "issues": [{"severity": "error", "code": "row_already_exists", "message": f"Row already exists for {patch.channel}/{patch.video}"}],
                    "changes": [],
                },
                2,
            )

        new_row: dict[str, str] = {h: "" for h in headers}

        if "チャンネル" in new_row and not (patch.values.get("チャンネル") or "").strip():
            new_row["チャンネル"] = patch.channel
        if "動画番号" in new_row and not (patch.values.get("動画番号") or "").strip():
            try:
                new_row["動画番号"] = str(int(patch.video))
            except Exception:
                new_row["動画番号"] = patch.video
        if "動画ID" in new_row and not (patch.values.get("動画ID") or "").strip():
            new_row["動画ID"] = f"{patch.channel}-{patch.video}"
        if "台本番号" in new_row and not (patch.values.get("台本番号") or "").strip():
            new_row["台本番号"] = f"{patch.channel}-{patch.video}"

        if "No." in new_row and not (patch.values.get("No.") or "").strip():
            max_no = 0
            for r in rows:
                raw = (r.get("No.") or "").strip()
                try:
                    max_no = max(max_no, int(raw))
                except Exception:
                    continue
            new_row["No."] = str((max_no or len(rows)) + 1)

        for col, value in patch.values.items():
            if col not in headers:
                if allow_new_columns:
                    headers.append(col)
                    new_row[col] = ""
                else:
                    issues.append(
                        {
                            "severity": "error",
                            "code": "unknown_column",
                            "message": f"Column not found in CSV header: {col!r} (use --allow-new-columns to append)",
                        }
                    )
                    continue
            new_row[col] = str(value or "")

        if not (new_row.get("タイトル") or "").strip():
            issues.append({"severity": "error", "code": "missing_title_after", "message": "タイトル would be empty"})

        rows.append(new_row)
        row_idx = len(rows) - 1
        row_after = dict(rows[row_idx])
        row_index_1based = row_idx + 1

        for col in headers:
            after = str(new_row.get(col) or "")
            if after:
                changes.append({"column": col, "before": "", "after": after})

    else:
        issues.append({"severity": "error", "code": "unsupported_op", "message": f"Unsupported op: {patch.op!r}"})

    ok = not any(it.get("severity") == "error" for it in issues)

    # Validate the candidate output with planning_lint, then (optionally) apply.
    if ok and changes:
        out_dir.mkdir(parents=True, exist_ok=True)
        if apply:
            with NamedTemporaryFile("w", encoding="utf-8-sig", newline="", suffix=".csv", dir=out_dir, delete=False) as tmp:
                candidate_csv_path = Path(tmp.name)
        else:
            ts = _utc_now_compact()
            candidate_csv_path = out_dir / f"planning_patch_{label}__candidate__{ts}.csv"

        _write_csv(candidate_csv_path, headers, rows)
        candidate_csv_meta = _file_meta(candidate_csv_path)

        try:
            import planning_lint as _planning_lint

            candidate_lint = _planning_lint.lint_planning_csv(candidate_csv_path, patch.channel)
            if isinstance(candidate_lint, dict) and not candidate_lint.get("ok", False):
                issues.append(
                    {
                        "severity": "error",
                        "code": "candidate_planning_lint_failed",
                        "message": "planning_lint failed on candidate CSV (patch would introduce errors)",
                        "details": {"csv": candidate_csv_meta, "counts": candidate_lint.get("counts")},
                    }
                )
        except Exception as e:
            lint_exception = repr(e)
            issues.append(
                {
                    "severity": "error" if apply else "warning",
                    "code": "candidate_planning_lint_exception",
                    "message": f"planning_lint exception: {lint_exception}",
                    "details": {"csv": candidate_csv_meta},
                }
            )

    ok = not any(it.get("severity") == "error" for it in issues)

    if ok and apply and changes:
        out_dir.mkdir(parents=True, exist_ok=True)
        backup_path = out_dir / f"backup_{patch.channel}_{patch.video}__{_safe_slug(patch.patch_id)}__{_utc_now_compact()}.csv"
        backup_path.write_text(csv_path.read_text(encoding="utf-8-sig"), encoding="utf-8-sig")
        backup_meta = _file_meta(backup_path)
        print(f"Backup: {backup_path}")
        _write_csv(csv_path, headers, rows)
        applied = True
        print(f"Applied: {csv_path}")

        try:
            import planning_lint as _planning_lint

            post_apply_lint = _planning_lint.lint_planning_csv(csv_path, patch.channel)
            if isinstance(post_apply_lint, dict) and not post_apply_lint.get("ok", False):
                issues.append(
                    {
                        "severity": "error",
                        "code": "post_apply_planning_lint_failed",
                        "message": "planning_lint failed after applying patch (unexpected; candidate lint should have caught this)",
                        "details": {"counts": post_apply_lint.get("counts")},
                    }
                )
                ok = False
        except Exception as e:
            lint_exception = repr(e)
            issues.append(
                {
                    "severity": "warning",
                    "code": "post_apply_planning_lint_exception",
                    "message": f"planning_lint exception after apply: {lint_exception}",
                }
            )

    # Apply mode uses a temporary candidate CSV; remove it on success to reduce clutter.
    if apply and candidate_csv_path is not None and applied and ok and candidate_csv_path.exists():
        try:
            candidate_csv_path.unlink()
            candidate_csv_deleted_after_apply = True
        except Exception:
            candidate_csv_deleted_after_apply = False

    payload: dict[str, Any] = {
        "schema": "ytm.planning_patch_apply.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ok": ok,
        "mode": "apply" if apply else "dry-run",
        "patch_id": patch.patch_id,
        "patch": {
            "path": str(patch_path),
            "file": _file_meta(patch_path),
            "patch_id": patch.patch_id,
            "target": {"channel": patch.channel, "video": patch.video},
            "apply": {patch.op: patch.values},
            "notes": patch.notes,
        },
        "target": {"channel": patch.channel, "video": patch.video},
        "csv_path": str(csv_path),
        "csv_file": _file_meta(csv_path),
        "row_index": row_index_1based,
        "issues": issues,
        "changes": changes,
        "row_before": before_row,
        "row_after": row_after,
        "candidate_csv": candidate_csv_meta,
        "candidate_planning_lint": candidate_lint,
        "candidate_csv_deleted_after_apply": candidate_csv_deleted_after_apply,
        "applied": applied,
        "backup_csv": backup_meta,
        "post_apply_planning_lint": post_apply_lint,
    }

    if apply and candidate_csv_path is not None and not applied and not ok:
        print(f"Candidate CSV kept for inspection: {candidate_csv_path}")

    if not ok:
        return _write_and_return(payload, 2)

    if apply and lint_exception is not None:
        return _write_and_return(payload, 1)

    return _write_and_return(payload, 0)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--patch", action="append", required=True, help="Patch YAML path (repo-relative or absolute)")
    ap.add_argument("--apply", action="store_true", help="Rewrite the CSV in-place")
    ap.add_argument("--allow-new-columns", action="store_true", help="Allow patch to append new CSV columns (use with care)")
    ap.add_argument("--write-latest", action="store_true", help="Also write *_latest.json/md (overwrite)")
    ap.add_argument("--ignore-locks", action="store_true", help="Ignore coordination locks (use with caution)")
    args = ap.parse_args(argv)

    exit_code = 0
    for raw in list(args.patch or []):
        patch_path = Path(raw).expanduser()
        if not patch_path.is_absolute():
            patch_path = repo_root() / patch_path

        patch, patch_issues = _load_patch(patch_path)
        if patch is None:
            out_dir = logs_root() / "regression" / "planning_patch"
            label = patch_path.stem
            payload = {
                "schema": "ytm.planning_patch_apply.v1",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "ok": False,
                "mode": "apply" if args.apply else "dry-run",
                "patch_path": str(patch_path),
                "patch_file": _file_meta(patch_path),
                "issues": patch_issues,
                "changes": [],
            }
            report_json, report_md = _write_report(payload, out_dir=out_dir, label=label, write_latest=bool(args.write_latest))
            print(f"Wrote: {report_json}")
            print(f"Wrote: {report_md}")
            exit_code = max(exit_code, 2)
            continue

        csv_path = channels_csv_path(patch.channel)
        payload, code = _apply_patch_to_csv(
            patch,
            patch_path=patch_path,
            csv_path=csv_path,
            apply=bool(args.apply),
            allow_new_columns=bool(args.allow_new_columns),
            ignore_locks=bool(args.ignore_locks),
            write_latest=bool(args.write_latest),
        )
        exit_code = max(exit_code, code)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

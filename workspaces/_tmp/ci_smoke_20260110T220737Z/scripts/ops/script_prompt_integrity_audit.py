#!/usr/bin/env python3
"""
script_prompt_integrity_audit.py — prompt loading/source-of-truth consistency audit (NO LLM).

Why:
- Prompt data is intentionally stored in multiple places for UI/ops convenience:
  - `configs/sources.yaml` (entry SSOT for runner)
  - `packages/script_pipeline/channels/CHxx-*/script_prompt.txt` (prompt SSOT)
  - `packages/script_pipeline/channels/CHxx-*/channel_info.json` (UI metadata; includes a copy of script_prompt)
  - `packages/script_pipeline/prompts/channels/CHxx.yaml` (optional legacy YAML input for channel_prompt_sync; runner does NOT read it)
- If these drift, humans (and some tools) can read the wrong prompt and accidentally overwrite the SSOT.

This tool audits those surfaces and can optionally sync the *copies* back to the SSOT
(`script_prompt.txt`) WITHOUT changing runtime behavior.

Usage:
  python3 scripts/ops/script_prompt_integrity_audit.py --all
  python3 scripts/ops/script_prompt_integrity_audit.py --channel CH05 --write-latest
  python3 scripts/ops/script_prompt_integrity_audit.py --all --apply-channel-info-sync --apply-yaml-sync

Outputs:
  workspaces/logs/regression/script_prompt_integrity/
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from _bootstrap import bootstrap

PROJECT_ROOT = bootstrap(load_env=False)

from factory_common.paths import logs_root, repo_root, script_pkg_root


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def load_sources_doc() -> dict[str, Any]:
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


def _channels_root() -> Path:
    return script_pkg_root() / "channels"


def _prompt_yaml_dir() -> Path:
    return script_pkg_root() / "prompts" / "channels"


def _normalize_channel_code(raw: str) -> str:
    s = (raw or "").strip().upper()
    if re.fullmatch(r"CH\\d{2}", s):
        return s
    m = re.fullmatch(r"CH(\\d+)", s)
    if m:
        return f"CH{int(m.group(1)):02d}"
    return s


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")


def _write_text(path: Path, text: str) -> None:
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.endswith("\n"):
        normalized += "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized, encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    return obj if isinstance(obj, dict) else {}


def _write_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class Issue:
    severity: str  # error|warning
    code: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {"severity": self.severity, "code": self.code, "message": self.message}


_PROMPT_BODY_LINE_RE = re.compile(r"^(?P<indent>\\s*)prompt_body:\\s*\\|[-+]?\\s*$")


def _replace_yaml_prompt_body(yaml_text: str, new_body: str) -> tuple[str, bool]:
    """
    Replace the block scalar under `prompt_body: |` while preserving the rest of the file.

    This avoids rewriting the whole YAML (key order/comments).
    """
    text = (yaml_text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.splitlines(keepends=True)

    idx: Optional[int] = None
    indent = ""
    for i, ln in enumerate(lines):
        m = _PROMPT_BODY_LINE_RE.match(ln.rstrip("\n"))
        if m:
            idx = i
            indent = m.group("indent") or ""
            break
    if idx is None:
        return yaml_text, False

    block_prefix = indent + "  "
    j = idx + 1
    while j < len(lines):
        ln = lines[j]
        if ln.strip() == "":
            j += 1
            continue
        if ln.startswith(block_prefix):
            j += 1
            continue
        break

    body_norm = (new_body or "").replace("\r\n", "\n").replace("\r", "\n")
    if not body_norm.endswith("\n"):
        body_norm += "\n"
    new_block: list[str] = []
    for raw in body_norm.split("\n")[:-1]:
        new_block.append(block_prefix + raw + "\n")

    new_lines = lines[: idx + 1] + new_block + lines[j:]
    out = "".join(new_lines)
    if not out.endswith("\n"):
        out += "\n"
    return out, out != text


def _iter_channel_dirs() -> list[Path]:
    root = _channels_root()
    if not root.exists():
        return []
    out: list[Path] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        if not entry.name.upper().startswith("CH"):
            continue
        if (entry / "channel_info.json").exists():
            out.append(entry)
    return out


def audit_and_optionally_fix(
    *,
    channels: set[str] | None,
    apply_channel_info_sync: bool,
    apply_yaml_sync: bool,
) -> dict[str, Any]:
    sources = load_sources_doc()
    sources_channels = sources.get("channels") if isinstance(sources.get("channels"), dict) else {}

    rows: list[dict[str, Any]] = []
    applied: list[dict[str, Any]] = []
    issues_all: list[Issue] = []

    for ch_dir in _iter_channel_dirs():
        info_path = ch_dir / "channel_info.json"
        try:
            info = _read_json(info_path)
        except Exception as exc:
            issues = [Issue("error", "invalid_channel_info_json", f"Invalid JSON: {info_path}: {exc!r}")]
            rows.append(
                {
                    "channel_dir": str(ch_dir.relative_to(repo_root())),
                    "issues": [i.as_dict() for i in issues],
                }
            )
            issues_all.extend(issues)
            continue

        ch_code = _normalize_channel_code(str(info.get("channel_id") or ch_dir.name.split("-", 1)[0]))
        if channels and ch_code not in channels:
            continue

        issues: list[Issue] = []

        template_rel = str(info.get("template_path") or "").strip()
        template_path = (repo_root() / template_rel) if template_rel else None
        prompt_file_path = ch_dir / "script_prompt.txt"

        # sources.yaml entry
        src = sources_channels.get(ch_code) if isinstance(sources_channels, dict) else None
        src_prompt_rel = str((src or {}).get("channel_prompt") or "").strip() if isinstance(src, dict) else ""
        src_prompt_path = (repo_root() / src_prompt_rel) if src_prompt_rel else None

        if not src_prompt_rel:
            issues.append(Issue("error", "missing_sources_channel_prompt", f"configs/sources.yaml missing channels.{ch_code}.channel_prompt"))
        if not template_rel:
            issues.append(Issue("error", "missing_channel_info_template_path", f"channel_info.json missing template_path: {info_path}"))

        if template_path and template_path.exists() and prompt_file_path.exists():
            try:
                if template_path.resolve() != prompt_file_path.resolve():
                    issues.append(
                        Issue(
                            "error",
                            "template_path_not_script_prompt",
                            f"channel_info.template_path does not point to channel_dir/script_prompt.txt: {template_rel}",
                        )
                    )
            except Exception:
                pass

        if src_prompt_path and template_path:
            try:
                if src_prompt_path.resolve() != template_path.resolve():
                    issues.append(
                        Issue(
                            "error",
                            "sources_prompt_mismatch",
                            "sources.yaml channel_prompt != channel_info.template_path "
                            f"(sources={src_prompt_rel} info={template_rel})",
                        )
                    )
            except Exception:
                pass

        if template_path and not template_path.exists():
            issues.append(Issue("error", "missing_template_path_file", f"Missing: {template_path}"))
        if src_prompt_path and not src_prompt_path.exists():
            issues.append(Issue("error", "missing_sources_prompt_file", f"Missing: {src_prompt_path}"))
        if not prompt_file_path.exists():
            issues.append(Issue("warning", "missing_script_prompt_txt", f"Missing: {prompt_file_path.relative_to(repo_root())}"))

        # channel_info.script_prompt copy drift
        prompt_text = ""
        if template_path and template_path.exists():
            try:
                prompt_text = _read_text(template_path)
            except Exception as exc:
                issues.append(Issue("error", "prompt_file_read_failed", f"Cannot read {template_path}: {exc!r}"))

        info_prompt = info.get("script_prompt")
        if isinstance(info_prompt, str) and prompt_text:
            if info_prompt.replace("\r\n", "\n").replace("\r", "\n").rstrip() != prompt_text.rstrip():
                issues.append(Issue("warning", "channel_info_script_prompt_drift", "channel_info.json script_prompt != template_path file"))
                if apply_channel_info_sync:
                    info["script_prompt"] = prompt_text
                    _write_json(info_path, info)
                    applied.append(
                        {
                            "channel": ch_code,
                            "action": "sync_channel_info_script_prompt",
                            "path": str(info_path.relative_to(repo_root())),
                        }
                    )
        elif prompt_text and not isinstance(info_prompt, str):
            issues.append(Issue("warning", "missing_channel_info_script_prompt", "channel_info.json missing script_prompt (UI may show empty prompt)"))
            if apply_channel_info_sync:
                info["script_prompt"] = prompt_text
                _write_json(info_path, info)
                applied.append(
                    {
                        "channel": ch_code,
                        "action": "seed_channel_info_script_prompt",
                        "path": str(info_path.relative_to(repo_root())),
                    }
                )

        # Optional YAML drift (legacy input for channel_prompt_sync)
        yaml_path = _prompt_yaml_dir() / f"{ch_code}.yaml"
        yaml_drift: bool | None = None
        if yaml_path.exists() and prompt_text:
            try:
                ydoc = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            except Exception as exc:
                issues.append(Issue("warning", "invalid_prompt_yaml", f"Invalid YAML: {yaml_path}: {exc!r}"))
                ydoc = {}
            cp = ydoc.get("channel_prompt") if isinstance(ydoc, dict) else None
            body = (cp or {}).get("prompt_body") if isinstance(cp, dict) else None
            body_norm = body.rstrip() + "\n" if isinstance(body, str) else ""
            yaml_drift = bool(body_norm) and (body_norm.rstrip() != prompt_text.rstrip())
            if yaml_drift:
                issues.append(Issue("warning", "prompt_yaml_drift", "prompt YAML (legacy) prompt_body != script_prompt.txt (SSOT)"))
                if apply_yaml_sync:
                    new_text, changed = _replace_yaml_prompt_body(yaml_path.read_text(encoding="utf-8"), prompt_text)
                    if not changed:
                        issues.append(Issue("warning", "prompt_yaml_sync_skipped", "prompt_body block not found; manual fix required"))
                    else:
                        # Validate YAML after edit (defensive).
                        try:
                            yaml.safe_load(new_text)
                        except Exception as exc:
                            issues.append(Issue("error", "prompt_yaml_sync_invalid", f"Refusing to write invalid YAML after sync: {exc!r}"))
                        else:
                            _write_text(yaml_path, new_text)
                            applied.append(
                                {
                                    "channel": ch_code,
                                    "action": "sync_prompt_yaml_prompt_body",
                                    "path": str(yaml_path.relative_to(repo_root())),
                                }
                            )

        row = {
            "channel": ch_code,
            "channel_dir": str(ch_dir.relative_to(repo_root())),
            "sources_channel_prompt": src_prompt_rel or None,
            "channel_info_template_path": template_rel or None,
            "prompt_yaml_path": str(yaml_path.relative_to(repo_root())) if yaml_path.exists() else None,
            "issues": [i.as_dict() for i in issues],
        }
        rows.append(row)
        issues_all.extend(issues)

    by_sev = Counter(i.severity for i in issues_all)
    by_code = Counter(i.code for i in issues_all)
    gate = "pass" if by_sev.get("error", 0) == 0 else "fail"
    return {
        "schema": "ytm.script_prompt_integrity_audit.v1",
        "generated_at": _utc_now_iso(),
        "channels_checked": len(rows),
        "gate": gate,
        "counts": {"by_severity": dict(by_sev), "by_code": dict(by_code)},
        "applied": applied,
        "rows": rows,
    }


def write_report(report: dict[str, Any], label: str, *, write_latest: bool) -> tuple[Path, Path]:
    out_dir = logs_root() / "regression" / "script_prompt_integrity"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = _utc_now_compact()
    json_path = out_dir / f"script_prompt_integrity_{label}__{ts}.json"
    md_path = out_dir / f"script_prompt_integrity_{label}__{ts}.md"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    counts = report.get("counts") if isinstance(report, dict) else {}
    rows = report.get("rows") if isinstance(report, dict) else []
    by_sev = counts.get("by_severity") if isinstance(counts, dict) else {}
    by_code = counts.get("by_code") if isinstance(counts, dict) else {}
    applied = report.get("applied") if isinstance(report, dict) else []

    lines: list[str] = []
    lines.append(f"# script_prompt_integrity report: {label}")
    lines.append("")
    lines.append(f"- generated_at: {report.get('generated_at')}")
    lines.append(f"- gate: {report.get('gate')}")
    lines.append(f"- channels_checked: {report.get('channels_checked')}")
    lines.append(f"- counts.by_severity: {json.dumps(by_sev, ensure_ascii=False)}")
    lines.append(f"- counts.by_code: {json.dumps(by_code, ensure_ascii=False)}")
    lines.append("")

    if isinstance(applied, list) and applied:
        lines.append("## Applied fixes (first 60)")
        for it in applied[:60]:
            if not isinstance(it, dict):
                continue
            lines.append(f"- {it.get('channel')}: {it.get('action')} → {it.get('path')}")
        lines.append("")

    lines.append("## Issues (first 160)")
    count = 0
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            ch = row.get("channel")
            for it in row.get("issues") or []:
                if not isinstance(it, dict):
                    continue
                sev = it.get("severity")
                code = it.get("code")
                msg = it.get("message")
                lines.append(f"- [{sev}] {ch} {code}: {msg}")
                count += 1
                if count >= 160:
                    break
            if count >= 160:
                break

    md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    if write_latest:
        latest_json = out_dir / f"script_prompt_integrity_{label}__latest.json"
        latest_md = out_dir / f"script_prompt_integrity_{label}__latest.md"
        latest_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        latest_md.write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")

    return json_path, md_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Audit prompt SSOT/copies consistency (no LLM).")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true", help="Audit all channels")
    g.add_argument("--channel", action="append", help="Audit only these channels (repeatable, e.g. --channel CH05)")
    ap.add_argument("--apply-channel-info-sync", action="store_true", help="Sync channel_info.json script_prompt from template_path file")
    ap.add_argument("--apply-yaml-sync", action="store_true", help="Sync legacy prompt YAML prompt_body from script_prompt.txt (SSOT)")
    ap.add_argument("--write-latest", action="store_true", help="Also write *_latest.json/md (overwrite)")
    ap.add_argument("--label", default="all", help="Label for output filenames (default: all)")
    args = ap.parse_args(argv)

    only: set[str] | None = None
    if args.channel:
        only = {_normalize_channel_code(x) for raw in args.channel for x in str(raw).replace(",", " ").split() if x.strip()}
        args.label = args.label or "selected"

    report = audit_and_optionally_fix(
        channels=only,
        apply_channel_info_sync=bool(args.apply_channel_info_sync),
        apply_yaml_sync=bool(args.apply_yaml_sync),
    )
    json_path, md_path = write_report(report, args.label, write_latest=bool(args.write_latest))
    print(f"Wrote: {json_path.relative_to(repo_root())}")
    print(f"Wrote: {md_path.relative_to(repo_root())}")
    return 0 if report.get("gate") == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())


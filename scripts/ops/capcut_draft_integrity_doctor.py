#!/usr/bin/env python3
"""
capcut_draft_integrity_doctor.py — Hot(未投稿)のCapCutドラフトを全件監査し、参照切れ/迷子/重複をレポート（read-only）

SSOT:
  - ssot/ops/OPS_CAPCUT_DRAFT_SOP.md
  - ssot/ops/OPS_LOGGING_MAP.md
  - ssot/ops/OPS_HOTSET_POLICY.md

Why:
  - 「完了と言ったのにCapCutで参照切れ」を再発させないため、機械監査で Fail-fast する。
  - draft_dir が増殖して “どれが正か分からない” をレポートで可視化する。

Policy:
  - read-only（修復は別ツール: scripts/ops/relink_capcut_draft.py / auto_capcut_run --resume 等）
  - 出力は `workspaces/logs/regression/capcut_draft_integrity/` に JSON+MD（latest pointer も上書き）
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from _bootstrap import bootstrap

_REPO_ROOT = bootstrap(load_env=False)

from factory_common.paths import capcut_draft_root, channels_csv_path, planning_root, status_path, workspace_root  # noqa: E402


FREEZE_SCHEMA = "ytm.hotset_freeze.v1"
REPORT_SCHEMA = "ytm.capcut_draft_integrity_doctor_report.v1"

_CAPCUT_PLACEHOLDER_TOKENS = ("##_material_placeholder_", "##_draftpath_placeholder_")


def _now_iso_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _utc_compact() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _z3(video: str) -> str:
    return str(video).zfill(3)


def _norm_channel(raw: str) -> str:
    s = str(raw or "").strip().upper()
    if not s.startswith("CH") or s == "CH":
        raise SystemExit(f"Invalid channel: {raw!r} (expected CHxx)")
    digits = "".join(ch for ch in s[2:] if ch.isdigit())
    if digits:
        return f"CH{int(digits):02d}"
    return s


def _norm_video(raw: str) -> str:
    token = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if not token:
        raise SystemExit(f"Invalid video: {raw!r} (expected NNN)")
    return _z3(str(int(token)))


def _is_published_progress(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return ("投稿済み" in text) or ("公開済み" in text) or (text.lower() in {"published", "posted"})


def _safe_read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _is_published_by_status_json(channel: str, video: str) -> bool:
    sp = status_path(channel, video)
    if not sp.exists():
        return False
    payload = _safe_read_json(sp)
    meta = payload.get("metadata") if isinstance(payload, dict) else None
    return isinstance(meta, dict) and bool(meta.get("published_lock"))


def _row_video_token(row: dict[str, str]) -> Optional[str]:
    for key in ("動画番号", "No.", "video", "Video", "VideoNumber", "video_number"):
        v = row.get(key)
        if not v:
            continue
        token = "".join(ch for ch in str(v) if ch.isdigit())
        if token:
            return _z3(str(int(token)))
    return None


def _row_title(row: dict[str, str]) -> str:
    for key in ("タイトル", "title", "Title"):
        v = row.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _local_planning_root() -> Path:
    # Prefer a repo-local planning copy when shared storage is unavailable.
    return workspace_root() / "planning"


def _planning_csv_path(channel: str) -> Path:
    """
    Resolve planning CSV for the channel with fallback:
      1) factory_common.paths.channels_csv_path (may point to shared storage)
      2) workspaces/planning/channels/<CHxx>.csv (repo-local mirror)
    """
    ch = _norm_channel(channel)
    primary = channels_csv_path(ch)
    if primary.exists():
        return primary
    fallback = _local_planning_root() / "channels" / f"{ch}.csv"
    if fallback.exists():
        return fallback
    # return primary for error message clarity
    return primary


def _freeze_path() -> Path:
    # Shared planning_root may be unavailable (e.g., external mount missing).
    primary = planning_root() / "hotset_freeze.json"
    if primary.exists():
        return primary
    fallback = _local_planning_root() / "hotset_freeze.json"
    return fallback


def _load_freeze_keys() -> set[tuple[str, str]]:
    path = _freeze_path()
    data = _safe_read_json(path) if path.exists() else {}
    schema = str(data.get("schema") or "").strip()
    if schema and schema != FREEZE_SCHEMA:
        return set()
    raw_items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(raw_items, list):
        return set()
    out: set[tuple[str, str]] = set()
    for it in raw_items:
        if not isinstance(it, dict):
            continue
        try:
            ch = _norm_channel(it.get("channel") or "")
            vv = _norm_video(it.get("video") or "")
        except Exception:
            continue
        out.add((ch, vv))
    return out


def _load_planning_rows(channel: str) -> list[dict[str, str]]:
    csv_path = _planning_csv_path(channel)
    if not csv_path.exists():
        alt = _local_planning_root() / "channels" / f"{_norm_channel(channel)}.csv"
        raise SystemExit(f"planning csv not found: {csv_path} (fallback tried: {alt})")
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader)


@dataclass(frozen=True)
class Episode:
    channel: str
    video: str
    title: str
    progress: str

    @property
    def episode_id(self) -> str:
        return f"{self.channel}-{self.video}"


def _iter_channels_from_planning() -> list[str]:
    roots = [planning_root() / "channels", _local_planning_root() / "channels"]
    out: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        for p in sorted(root.iterdir()):
            if not p.is_file():
                continue
            if not p.name.upper().startswith("CH") or not p.name.lower().endswith(".csv"):
                continue
            out.append(_norm_channel(p.stem))
    # unique / stable order
    seen: set[str] = set()
    deduped: list[str] = []
    for ch in out:
        if ch in seen:
            continue
        seen.add(ch)
        deduped.append(ch)
    return deduped


def _hot_episodes(channel: str) -> list[Episode]:
    ch = _norm_channel(channel)
    freeze = _load_freeze_keys()
    rows = _load_planning_rows(ch)
    out: list[Episode] = []
    for row in rows:
        vid = _row_video_token(row)
        if not vid:
            continue
        progress = (row.get("進捗") or row.get("progress") or "").strip()
        title = _row_title(row)
        if _is_published_progress(progress) or _is_published_by_status_json(ch, vid):
            continue
        if (ch, vid) in freeze:
            continue
        out.append(Episode(channel=ch, video=vid, title=title, progress=progress))
    return out


def _read_status_video_run_id(channel: str, video: str) -> Optional[str]:
    sp = status_path(channel, video)
    if not sp.exists():
        return None
    payload = _safe_read_json(sp)
    meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None
    if not isinstance(meta, dict):
        return None
    run_id = str(meta.get("video_run_id") or "").strip()
    return run_id or None


def _resolve_run_dir(channel: str, video: str) -> tuple[Optional[str], Optional[Path]]:
    run_id = _read_status_video_run_id(channel, video)
    if not run_id:
        return None, None
    run_dir = (workspace_root() / "video" / "runs" / run_id).resolve()
    return run_id, run_dir if run_dir.exists() else run_dir


def _candidate_drafts(episode_id: str) -> list[Path]:
    root = capcut_draft_root().expanduser()
    if not root.exists():
        return []
    token = episode_id.upper()
    out: list[Path] = []
    for p in root.iterdir():
        try:
            if not p.is_dir():
                continue
        except Exception:
            continue
        if token in p.name.upper():
            out.append(p)
    out.sort(key=lambda p: (p.stat().st_mtime if p.exists() else 0.0, p.name), reverse=True)
    return out


def _safe_readlink(path: Path) -> Optional[str]:
    try:
        if not path.is_symlink():
            return None
        return os.readlink(path)
    except OSError:
        return None


def _resolve_run_capcut_draft_path(run_dir: Path) -> Optional[str]:
    info = run_dir / "capcut_draft_info.json"
    if info.exists():
        payload = _safe_read_json(info)
        val = payload.get("draft_path")
        if isinstance(val, str) and val.strip():
            return val.strip()
    link = run_dir / "capcut_draft"
    target = _safe_readlink(link)
    return target.strip() if target else None


def _iter_strings(obj: Any) -> Iterable[str]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _iter_strings(k)
            yield from _iter_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_strings(v)
    elif isinstance(obj, str):
        yield obj


_REL_PREFIXES = ("assets/", "materials/", "common_attachment/")
_MEDIA_EXT_RE = re.compile(r"\.(png|jpg|jpeg|webp|mp4|mov|wav|mp3|srt)$", re.IGNORECASE)


@dataclass(frozen=True)
class DraftIntegrity:
    draft_dir: str
    ok: bool
    required_missing: list[str]
    json_parse_errors: list[str]
    missing_internal_refs: list[str]
    zero_byte_internal_refs: list[str]
    placeholder_path_refs: list[str]
    external_missing_refs: list[str]  # warn-only (e.g. original file_Path)
    notes: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "draft_dir": self.draft_dir,
            "ok": self.ok,
            "required_missing": self.required_missing,
            "json_parse_errors": self.json_parse_errors,
            "missing_internal_refs": self.missing_internal_refs,
            "zero_byte_internal_refs": self.zero_byte_internal_refs,
            "placeholder_path_refs": self.placeholder_path_refs,
            "external_missing_refs": self.external_missing_refs,
            "notes": self.notes,
        }


def _audit_draft_dir(draft_dir: Path, *, verbose: bool) -> DraftIntegrity:
    required = ["draft_info.json", "draft_content.json", "draft_meta_info.json"]
    required_missing = [name for name in required if not (draft_dir / name).exists()]

    json_parse_errors: list[str] = []
    missing_internal: list[str] = []
    zero_byte_internal: list[str] = []
    placeholder_refs: list[str] = []
    external_missing: list[str] = []
    notes: list[str] = []
    meta_issues: list[str] = []

    # Fast check: if required is missing, still continue to collect what we can.
    json_files = [p for p in draft_dir.rglob("*.json") if p.is_file()]
    # Prefer stable order
    json_files.sort(key=lambda p: (len(p.parts), p.name))

    # Internal refs are those under the draft_dir (relative assets/materials).
    internal_checked: set[str] = set()
    external_checked: set[str] = set()

    for jp in json_files:
        try:
            payload = json.loads(jp.read_text(encoding="utf-8"))
        except Exception:
            # Some CapCut json files can legitimately be empty (0 bytes).
            # Example: draft_biz_config.json often exists as an empty placeholder.
            try:
                if jp.name in {"draft_biz_config.json"} and jp.stat().st_size == 0:
                    continue
            except Exception:
                pass
            # CapCut can also be writing; treat as error but keep scanning others.
            json_parse_errors.append(str(jp))
            continue

        # Special: draft_meta_info sanity checks
        if jp.name == "draft_meta_info.json" and isinstance(payload, dict):
            fold = payload.get("draft_fold_path")
            if isinstance(fold, str) and fold.strip():
                if Path(fold).expanduser().resolve() != draft_dir.resolve():
                    meta_issues.append(f"draft_fold_path_mismatch: {fold}")

            # draft_id / draft_name should match draft_info.json (primary key + listing consistency)
            info_path = draft_dir / "draft_info.json"
            try:
                info = json.loads(info_path.read_text(encoding="utf-8")) if info_path.exists() else {}
            except Exception:
                info = {}
            if isinstance(info, dict):
                meta_id = str(payload.get("draft_id") or "").strip()
                info_id = str(info.get("draft_id") or "").strip()
                if meta_id and info_id and meta_id != info_id:
                    meta_issues.append(f"draft_id_mismatch: meta={meta_id} info={info_id}")

                meta_name = str(payload.get("draft_name") or "").strip()
                info_name = str(info.get("draft_name") or "").strip()
                if meta_name and info_name and meta_name != info_name:
                    meta_issues.append("draft_name_mismatch")

        for s in _iter_strings(payload):
            raw = str(s or "")
            if len(raw) < 4:
                continue
            # Normalize file://
            if raw.startswith("file://"):
                raw = raw[len("file://") :]

            # CapCut placeholder tokens often indicate broken drafts (UI shows missing media).
            if any(tok in raw for tok in _CAPCUT_PLACEHOLDER_TOKENS):
                placeholder_refs.append(raw)

            # Internal relative refs (assets/materials/common_attachment).
            if raw.startswith(_REL_PREFIXES) and (_MEDIA_EXT_RE.search(raw) or "/" in raw):
                rel = raw
                if rel in internal_checked:
                    continue
                internal_checked.add(rel)
                p = draft_dir / rel
                if not p.exists():
                    missing_internal.append(rel)
                else:
                    try:
                        if p.is_file() and p.stat().st_size == 0:
                            zero_byte_internal.append(rel)
                    except Exception:
                        pass
                continue

            # Sometimes embedded with extra prefix; best-effort extract.
            if "assets/" in raw or "materials/" in raw or "common_attachment/" in raw:
                for pref in _REL_PREFIXES:
                    idx = raw.find(pref)
                    if idx < 0:
                        continue
                    rel = raw[idx:]
                    if not (_MEDIA_EXT_RE.search(rel) or "/" in rel):
                        continue
                    if rel in internal_checked:
                        continue
                    internal_checked.add(rel)
                    p = draft_dir / rel
                    if not p.exists():
                        missing_internal.append(rel)
                    else:
                        try:
                            if p.is_file() and p.stat().st_size == 0:
                                zero_byte_internal.append(rel)
                        except Exception:
                            pass
                continue

            # External absolute refs (warn-only). Only check “file-like” entries.
            if raw.startswith("/") and _MEDIA_EXT_RE.search(raw):
                if raw in external_checked:
                    continue
                external_checked.add(raw)
                p = Path(raw).expanduser()
                # External refs may be stale; warn-only.
                if not p.exists():
                    external_missing.append(raw)

    ok = (
        not required_missing
        and not json_parse_errors
        and not missing_internal
        and not zero_byte_internal
        and not placeholder_refs
        and not meta_issues
    )
    if not ok and verbose:
        if required_missing:
            notes.append(f"required_missing={len(required_missing)}")
        if json_parse_errors:
            notes.append(f"json_parse_errors={len(json_parse_errors)}")
        if missing_internal:
            notes.append(f"missing_internal_refs={len(missing_internal)}")
        if zero_byte_internal:
            notes.append(f"zero_byte_internal_refs={len(zero_byte_internal)}")
        if placeholder_refs:
            notes.append(f"placeholder_path_refs={len(placeholder_refs)}")
        if external_missing:
            notes.append(f"external_missing_refs={len(external_missing)}")
        if meta_issues:
            notes.append(f"meta_issues={len(meta_issues)}")

    # De-dupe with stable order
    def _dedup(xs: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for x in xs:
            if x in seen:
                continue
            seen.add(x)
            out.append(x)
        return out

    return DraftIntegrity(
        draft_dir=str(draft_dir),
        ok=bool(ok),
        required_missing=_dedup(required_missing),
        json_parse_errors=_dedup(json_parse_errors),
        missing_internal_refs=_dedup(missing_internal),
        zero_byte_internal_refs=_dedup(zero_byte_internal),
        placeholder_path_refs=_dedup(placeholder_refs),
        external_missing_refs=_dedup(external_missing),
        notes=_dedup(notes + meta_issues),
    )


@dataclass(frozen=True)
class EpisodeAudit:
    episode: Episode
    run_id: Optional[str]
    run_dir: Optional[str]
    run_capcut_draft_path: Optional[str]
    candidates: list[str]
    canonical: Optional[str]
    canonical_ok: Optional[bool]
    draft_reports: list[DraftIntegrity]

    def as_dict(self) -> dict[str, Any]:
        return {
            "episode": self.episode.episode_id,
            "title": self.episode.title,
            "progress": self.episode.progress,
            "run_id": self.run_id,
            "run_dir": self.run_dir,
            "run_capcut_draft_path": self.run_capcut_draft_path,
            "candidates": self.candidates,
            "canonical": self.canonical,
            "canonical_ok": self.canonical_ok,
            "draft_reports": [d.as_dict() for d in self.draft_reports],
        }


def _audit_episode(ep: Episode, *, verbose: bool, limit_candidates: int) -> EpisodeAudit:
    run_id, run_dir = _resolve_run_dir(ep.channel, ep.video)
    run_dir_s = str(run_dir) if run_dir is not None else None
    run_capcut_path: Optional[str] = None
    if run_dir is not None and run_dir.exists():
        run_capcut_path = _resolve_run_capcut_draft_path(run_dir)

    candidates = _candidate_drafts(ep.episode_id)
    cand_paths = [str(p) for p in candidates[: max(0, int(limit_candidates))]]

    reports: list[DraftIntegrity] = []
    by_dir: dict[str, DraftIntegrity] = {}
    for p in candidates[: max(0, int(limit_candidates))]:
        rep = _audit_draft_dir(p, verbose=verbose)
        reports.append(rep)
        by_dir[str(p.resolve())] = rep

    canonical: Optional[str] = None
    canonical_ok: Optional[bool] = None
    if run_capcut_path:
        canonical = run_capcut_path
        rep = by_dir.get(str(Path(run_capcut_path).expanduser().resolve()))
        canonical_ok = rep.ok if rep else (Path(run_capcut_path).exists() if run_capcut_path else None)
    else:
        # If run has no draft wiring, pick newest OK draft as a hint (still ambiguous).
        for rep in reports:
            if rep.ok:
                canonical = rep.draft_dir
                canonical_ok = True
                break

    return EpisodeAudit(
        episode=ep,
        run_id=run_id,
        run_dir=run_dir_s,
        run_capcut_draft_path=run_capcut_path,
        candidates=cand_paths,
        canonical=canonical,
        canonical_ok=canonical_ok,
        draft_reports=reports,
    )


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _report_dir() -> Path:
    return workspace_root() / "logs" / "regression" / "capcut_draft_integrity"


def _render_md(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# CapCut Draft Integrity Report ({report.get('scope','')})")
    lines.append("")
    lines.append(f"- generated_at: {report.get('generated_at','')}")
    lines.append(f"- host: {report.get('host','')}")
    lines.append(f"- capcut_draft_root: `{report.get('capcut_draft_root','')}`")
    lines.append(f"- hot_episodes: {report.get('hot_episodes',0)}")
    lines.append(f"- ok: {report.get('ok_count',0)} / bad: {report.get('bad_count',0)} / missing_draft: {report.get('missing_draft_count',0)}")
    lines.append("")

    bad = report.get("bad") or []
    missing = report.get("missing_draft") or []
    if bad:
        lines.append("## Failures")
        for it in bad[:200]:
            lines.append(f"- {it.get('episode')}: canonical_ok={it.get('canonical_ok')} candidates={len(it.get('candidates') or [])}")
            # show first missing refs if present
            reps = it.get("draft_reports") or []
            if reps:
                rep0 = reps[0]
                miss = (rep0.get("missing_internal_refs") or [])[:5]
                if miss:
                    lines.append(f"  - missing_internal_refs(sample): {miss}")
                ph = (rep0.get("placeholder_path_refs") or [])[:3]
                if ph:
                    lines.append(f"  - placeholder_path_refs(sample): {ph}")
    if missing:
        lines.append("")
        lines.append("## Missing Draft Wiring (run_dir has no draft)")
        for it in missing[:200]:
            lines.append(f"- {it.get('episode')}: run_id={it.get('run_id')} candidates={len(it.get('candidates') or [])}")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit CapCut drafts for Hot(未投稿) episodes (read-only).")
    g = ap.add_mutually_exclusive_group(required=False)
    g.add_argument("--channel", help="Channel to audit (CHxx).")
    g.add_argument("--all-channels", action="store_true", help="Audit all channels from planning/channels/*.csv")
    ap.add_argument("--limit", type=int, default=5000, help="Max hot episodes to inspect per channel (default: 5000).")
    ap.add_argument("--limit-candidates", type=int, default=10, help="Max CapCut draft candidates to scan per episode (default: 10).")
    ap.add_argument("--verbose", action="store_true", help="Include noisy details in notes.")
    ap.add_argument("--json", action="store_true", help="Print JSON to stdout (still writes files).")
    args = ap.parse_args()

    channels: list[str] = []
    if args.channel:
        channels = [_norm_channel(args.channel)]
    elif bool(args.all_channels):
        channels = _iter_channels_from_planning()
    else:
        # Default: all channels (safer for '未投稿のドラフト全て')
        channels = _iter_channels_from_planning()

    all_eps: list[Episode] = []
    for ch in channels:
        eps = _hot_episodes(ch)
        if int(args.limit) > 0:
            eps = eps[: int(args.limit)]
        all_eps.extend(eps)

    audits: list[EpisodeAudit] = []
    for ep in all_eps:
        audits.append(_audit_episode(ep, verbose=bool(args.verbose), limit_candidates=int(args.limit_candidates)))

    # Aggregate
    ok: list[dict[str, Any]] = []
    bad: list[dict[str, Any]] = []
    missing_draft: list[dict[str, Any]] = []
    for a in audits:
        d = a.as_dict()
        if not a.run_capcut_draft_path:
            missing_draft.append(d)
            continue
        if a.canonical_ok is True:
            ok.append(d)
        else:
            bad.append(d)

    report = {
        "schema": REPORT_SCHEMA,
        "generated_at": _now_iso_utc(),
        "scope": "hot_all" if (not args.channel) else f"hot_{_norm_channel(args.channel)}",
        "host": socket.gethostname(),
        "capcut_draft_root": str(capcut_draft_root()),
        "hot_episodes": len(audits),
        "ok_count": len(ok),
        "bad_count": len(bad),
        "missing_draft_count": len(missing_draft),
        "ok": ok,
        "bad": bad,
        "missing_draft": missing_draft,
    }

    out_dir = _report_dir()
    tag = _utc_compact()
    scope = str(report["scope"])
    json_path = out_dir / f"capcut_draft_integrity_{scope}__{tag}.json"
    md_path = out_dir / f"capcut_draft_integrity_{scope}__{tag}.md"
    latest_json = out_dir / f"capcut_draft_integrity_{scope}__latest.json"
    latest_md = out_dir / f"capcut_draft_integrity_{scope}__latest.md"

    _write_json(json_path, report)
    _write_json(latest_json, report)
    _write_text(md_path, _render_md(report))
    _write_text(latest_md, _render_md(report))

    if bool(args.json):
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"[capcut_draft_integrity] scope={scope} hot={len(audits)} ok={len(ok)} bad={len(bad)} missing_draft={len(missing_draft)}")
        print(f"- report: {json_path}")
        print(f"- latest: {latest_json}")

    return 0 if not bad else 2


if __name__ == "__main__":
    raise SystemExit(main())

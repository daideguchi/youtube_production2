#!/usr/bin/env python3
"""
hot_assets_doctor.py — Hot(未投稿)の資産がMacローカルに実体として存在するか検査（read-only）

SSOT:
  - ssot/ops/OPS_HOTSET_POLICY.md

Why:
  - 外部（Lenovo/NAS/共有）が不安定でも、Macの作業が止まらないことが最優先。
  - Hot(未投稿)が「外部だけ」に置かれている状態をP0として検知・可視化する。
  - このツールは移動/削除/修復をしない（read-only）。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from _bootstrap import bootstrap

_REPO_ROOT = bootstrap(load_env=False)

from factory_common.path_ref import is_path_ref, resolve_path_ref  # noqa: E402
from factory_common.paths import (  # noqa: E402
    capcut_draft_root,
    channels_csv_path,
    planning_root,
    shared_storage_root,
    status_path,
    video_capcut_local_drafts_root,
    video_runs_root,
    workspace_root,
)
from factory_common.timeline_manifest import parse_episode_id  # noqa: E402


FREEZE_SCHEMA = "ytm.hotset_freeze.v1"
REPORT_SCHEMA = "ytm.hot_assets_doctor_report.v1"


def _now_iso_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


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
    for key in ("動画ID", "台本番号", "ScriptID", "script_id"):
        raw = (row.get(key) or "").strip()
        ep = parse_episode_id(raw)
        if ep:
            return _z3(ep.video)
    return None


def _row_title(row: dict[str, str]) -> str:
    for key in ("タイトル", "title", "Title"):
        v = row.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _freeze_path() -> Path:
    return planning_root() / "hotset_freeze.json"


def _load_freeze_keys() -> set[tuple[str, str]]:
    path = _freeze_path()
    data = _safe_read_json(path) if path.exists() else {}
    schema = str(data.get("schema") or "").strip()
    if schema and schema != FREEZE_SCHEMA:
        # Tolerate unknown schema; treat as empty.
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
    csv_path = channels_csv_path(channel)
    if not csv_path.exists():
        raise SystemExit(f"planning csv not found: {csv_path}")
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader)


@dataclass(frozen=True)
class Episode:
    channel: str
    video: str
    progress: str
    title: str

    @property
    def episode_id(self) -> str:
        return f"{self.channel}-{self.video}"


@dataclass(frozen=True)
class RunPick:
    run_id: str
    run_dir: Path
    mtime: float
    has_draft_info: bool


def _resolve_episode_from_timeline_manifest(run_dir: Path) -> Optional[tuple[str, str]]:
    tm = run_dir / "timeline_manifest.json"
    if not tm.exists():
        return None
    data = _safe_read_json(tm)
    ep_raw = data.get("episode") if isinstance(data.get("episode"), dict) else None
    if isinstance(ep_raw, dict):
        ep_id = str(ep_raw.get("id") or "").strip()
        ep = parse_episode_id(ep_id)
        if ep:
            return ep.channel, ep.video
    return None


def _run_dir_episode(run_dir: Path) -> Optional[tuple[str, str]]:
    ep = parse_episode_id(run_dir.name)
    if ep:
        return ep.channel, ep.video
    return _resolve_episode_from_timeline_manifest(run_dir)


def _index_best_runs(channel: str, *, wanted_videos: set[str]) -> dict[str, RunPick]:
    root = video_runs_root()
    if not root.exists():
        return {}
    out: dict[str, RunPick] = {}
    for run_dir in sorted(root.iterdir()):
        if not run_dir.is_dir():
            continue
        if run_dir.name.startswith(("_", ".")):
            continue
        resolved = _run_dir_episode(run_dir)
        if not resolved:
            continue
        ch2, vid2 = resolved
        if ch2 != channel:
            continue
        if wanted_videos and vid2 not in wanted_videos:
            continue
        info_path = run_dir / "capcut_draft_info.json"
        has_info = info_path.exists()
        try:
            mtime = run_dir.stat().st_mtime
        except Exception:
            mtime = 0.0
        candidate = RunPick(run_id=run_dir.name, run_dir=run_dir, mtime=mtime, has_draft_info=bool(has_info))
        prev = out.get(vid2)
        if prev is None:
            out[vid2] = candidate
            continue
        if (candidate.has_draft_info, candidate.mtime, candidate.run_id) > (prev.has_draft_info, prev.mtime, prev.run_id):
            out[vid2] = candidate
    return out


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except Exception:
        return False
    return True


def _local_roots() -> list[Path]:
    # "Macローカル" として stat しても安全なルート（ネットワーク共有は除外する）
    roots: list[Path] = [
        workspace_root().expanduser(),
        Path.home().expanduser(),
        capcut_draft_root().expanduser(),
        video_capcut_local_drafts_root().expanduser(),
    ]
    return roots


def _classify_path(path: Path, *, shared_root: Optional[Path], local_roots: list[Path]) -> tuple[str, Optional[bool]]:
    """
    Return (location, exists_or_none).
      - location: shared | local | unknown
      - exists_or_none: bool if we decided it's safe to stat; None otherwise
    """
    p = path.expanduser()
    if shared_root is not None:
        sr = shared_root.expanduser()
        if _is_under(p, sr):
            return "shared", None

    for r in local_roots:
        try:
            if _is_under(p, r):
                try:
                    return "local", bool(p.exists())
                except Exception:
                    return "local", None
        except Exception:
            continue
    return "unknown", None


def _resolve_draft_path(info: dict[str, Any]) -> tuple[Optional[Path], Optional[dict[str, Any]], Optional[str]]:
    draft_ref = info.get("draft_path_ref") if isinstance(info, dict) else None
    legacy = str(info.get("draft_path") or "").strip() if isinstance(info, dict) else ""
    if is_path_ref(draft_ref):
        resolved = resolve_path_ref(draft_ref)
        if resolved is not None:
            return resolved, draft_ref, legacy or None
    if legacy:
        try:
            return Path(legacy).expanduser(), None, legacy
        except Exception:
            return None, None, legacy
    return None, None, None


def _symlink_target_abs(link: Path, target_raw: str) -> Path:
    tp = Path(target_raw).expanduser()
    if tp.is_absolute():
        return tp
    return (link.parent / tp).expanduser()


def _collect_channel(channel: str, *, limit: int) -> dict[str, Any]:
    ch = _norm_channel(channel)
    freeze = _load_freeze_keys()
    rows = _load_planning_rows(ch)

    planned: list[Episode] = []
    hot: list[Episode] = []
    frozen: list[Episode] = []
    published: list[Episode] = []

    for row in rows:
        vid = _row_video_token(row)
        if not vid:
            continue
        progress = str((row.get("進捗") or row.get("progress") or "")).strip()
        title = _row_title(row)
        ep = Episode(channel=ch, video=vid, progress=progress, title=title)
        planned.append(ep)

        if _is_published_progress(progress) or _is_published_by_status_json(ch, vid):
            published.append(ep)
            continue
        if (ch, vid) in freeze:
            frozen.append(ep)
            continue
        hot.append(ep)

    wanted_videos = {ep.video for ep in hot}
    best_runs = _index_best_runs(ch, wanted_videos=wanted_videos)

    shared_root = shared_storage_root()
    local_roots = _local_roots()

    violations: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    checked = 0
    truncated = False

    for ep in hot:
        if checked >= int(limit):
            truncated = True
            break
        checked += 1

        run = best_runs.get(ep.video)
        if run is None:
            warnings.append(
                {
                    "episode_id": ep.episode_id,
                    "kind": "video_run_missing",
                    "details": {"note": "No video run found yet (video pipeline may be unstarted)."},
                }
            )
            continue

        run_dir = run.run_dir
        # Validate run_dir/capcut_draft wiring when present (UI事故の早期検知).
        capcut_link = run_dir / "capcut_draft"
        if capcut_link.is_symlink():
            try:
                target_raw = os.readlink(capcut_link)
            except OSError:
                target_raw = ""
            if target_raw:
                target_abs = _symlink_target_abs(capcut_link, target_raw)
                loc, exists_flag = _classify_path(target_abs, shared_root=shared_root, local_roots=local_roots)
                if loc == "shared":
                    violations.append(
                        {
                            "episode_id": ep.episode_id,
                            "kind": "run_dir_symlink_points_to_shared_storage",
                            "details": {"run_id": run.run_id, "symlink": str(capcut_link), "target": str(target_abs)},
                        }
                    )
                elif loc == "local":
                    if exists_flag is False:
                        violations.append(
                            {
                                "episode_id": ep.episode_id,
                                "kind": "run_dir_symlink_broken",
                                "details": {"run_id": run.run_id, "symlink": str(capcut_link), "target": str(target_abs)},
                            }
                        )
                    elif exists_flag is None:
                        warnings.append(
                            {
                                "episode_id": ep.episode_id,
                                "kind": "run_dir_symlink_unstatable",
                                "details": {"run_id": run.run_id, "symlink": str(capcut_link), "target": str(target_abs)},
                            }
                        )
                else:
                    warnings.append(
                        {
                            "episode_id": ep.episode_id,
                            "kind": "run_dir_symlink_target_unknown",
                            "details": {"run_id": run.run_id, "symlink": str(capcut_link), "target": str(target_abs)},
                        }
                    )
            else:
                warnings.append(
                    {
                        "episode_id": ep.episode_id,
                        "kind": "run_dir_symlink_unreadable",
                        "details": {"run_id": run.run_id, "symlink": str(capcut_link)},
                    }
                )
        elif capcut_link.exists() and not capcut_link.is_dir():
            warnings.append(
                {
                    "episode_id": ep.episode_id,
                    "kind": "run_dir_capcut_draft_not_dir",
                    "details": {"run_id": run.run_id, "path": str(capcut_link)},
                }
            )

        info_path = run_dir / "capcut_draft_info.json"
        if not info_path.exists():
            # Not started / not using CapCut linking in this run yet.
            continue
        info = _safe_read_json(info_path)
        draft_path, draft_ref, legacy = _resolve_draft_path(info)
        if draft_path is None:
            warnings.append(
                {
                    "episode_id": ep.episode_id,
                    "kind": "draft_ref_missing",
                    "details": {"run_id": run.run_id, "run_dir": str(run_dir), "info_path": str(info_path)},
                }
            )
        else:
            loc, exists_flag = _classify_path(draft_path, shared_root=shared_root, local_roots=local_roots)
            if loc == "shared":
                violations.append(
                    {
                        "episode_id": ep.episode_id,
                        "kind": "draft_points_to_shared_storage",
                        "details": {
                            "run_id": run.run_id,
                            "run_dir": str(run_dir),
                            "draft_path": str(draft_path),
                            "draft_path_ref": draft_ref,
                            "legacy_draft_path": legacy,
                        },
                    }
                )
            elif loc == "local":
                if exists_flag is False:
                    violations.append(
                        {
                            "episode_id": ep.episode_id,
                            "kind": "draft_missing_locally",
                            "details": {
                                "run_id": run.run_id,
                                "run_dir": str(run_dir),
                                "draft_path": str(draft_path),
                                "draft_path_ref": draft_ref,
                                "legacy_draft_path": legacy,
                            },
                        }
                    )
                elif exists_flag is None:
                    warnings.append(
                        {
                            "episode_id": ep.episode_id,
                            "kind": "draft_unstatable",
                            "details": {"run_id": run.run_id, "draft_path": str(draft_path)},
                        }
                    )
            else:
                warnings.append(
                    {
                        "episode_id": ep.episode_id,
                        "kind": "draft_location_unknown",
                        "details": {"run_id": run.run_id, "draft_path": str(draft_path)},
                    }
                )

    return {
        "channel": ch,
        "planning_csv_path": str(channels_csv_path(ch)),
        "freeze_file": str(_freeze_path()),
        "counts": {
            "planned": len(planned),
            "hot": len(hot),
            "freeze": len(frozen),
            "published": len(published),
            "checked_hot": checked,
            "violations": len(violations),
            "warnings": len(warnings),
        },
        "truncated": bool(truncated),
        "violations": violations,
        "warnings": warnings,
    }


def _iter_planning_channels() -> list[str]:
    root = planning_root() / "channels"
    if not root.exists():
        return []
    out: list[str] = []
    for p in sorted(root.iterdir()):
        if not p.is_file():
            continue
        if p.suffix.lower() != ".csv":
            continue
        out.append(_norm_channel(p.stem))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Doctor for P0 invariant: Hot assets must exist locally on Mac (read-only).")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--channel", help="Channel (CHxx)")
    g.add_argument("--all-channels", action="store_true", help="Scan all Planning channels/*.csv")
    ap.add_argument("--limit", type=int, default=200, help="Max hot episodes to check per channel (default: 200)")
    ap.add_argument("--json", action="store_true", help="Emit JSON report (default: human-readable)")
    ap.add_argument(
        "--verbose",
        action="store_true",
        help="Print warnings verbosely (default suppresses noisy 'video_run_missing' list).",
    )
    args = ap.parse_args()

    channels = [_norm_channel(args.channel)] if args.channel else _iter_planning_channels()
    if not channels:
        raise SystemExit("No channels found (planning_root/channels missing).")

    report = {
        "schema": REPORT_SCHEMA,
        "generated_at": _now_iso_utc(),
        "host": {"hostname": socket.gethostname(), "platform": sys.platform},
        "repo": {"root": str(_REPO_ROOT)},
        "paths": {
            "workspace_root": str(workspace_root()),
            "planning_root": str(planning_root()),
            "video_runs_root": str(video_runs_root()),
            "capcut_draft_root": str(capcut_draft_root()),
            "capcut_local_drafts_root": str(video_capcut_local_drafts_root()),
            "shared_storage_root": str(shared_storage_root()) if shared_storage_root() is not None else None,
        },
        "channels": [],
    }

    any_violation = False
    for ch in channels:
        payload = _collect_channel(ch, limit=int(args.limit))
        any_violation = any_violation or bool(payload["counts"]["violations"])
        report["channels"].append(payload)

    if bool(args.json):
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2 if any_violation else 0

    print("[hot_assets_doctor] read-only")
    for ch_payload in report["channels"]:
        ch = ch_payload["channel"]
        c = ch_payload["counts"]
        print(f"\n[channel] {ch}")
        print(
            "- planned={planned} hot={hot} freeze={freeze} published={published} checked_hot={checked_hot}".format(**c)
        )
        if ch_payload.get("truncated"):
            print(f"- note: truncated (limit={args.limit})")
        if c["violations"]:
            print("[VIOLATIONS]")
            for v in ch_payload["violations"][:50]:
                ep = v.get("episode_id")
                kind = v.get("kind")
                details = v.get("details") or {}
                draft_path = details.get("draft_path") or details.get("target") or ""
                run_id = details.get("run_id") or ""
                print(f"- {ep}: {kind} run={run_id} path={draft_path}")
        if c["warnings"]:
            print("[WARNINGS]")
            warnings_list: list[dict[str, Any]] = ch_payload["warnings"]
            by_kind: dict[str, int] = {}
            for w in warnings_list:
                token = str(w.get("kind") or "").strip() or "unknown"
                by_kind[token] = by_kind.get(token, 0) + 1
            for k, n in sorted(by_kind.items(), key=lambda kv: (-kv[1], kv[0]))[:8]:
                print(f"- {k}: {n}")

            if not bool(args.verbose) and by_kind.get("video_run_missing"):
                print("- note: 'video_run_missing' is suppressed (use --verbose or --json).")

            shown = 0
            for w in warnings_list:
                ep = w.get("episode_id")
                kind = w.get("kind")
                if not bool(args.verbose) and str(kind) == "video_run_missing":
                    continue
                print(f"- {ep}: {kind}")
                shown += 1
                if shown >= 30:
                    break

    return 2 if any_violation else 0


if __name__ == "__main__":
    raise SystemExit(main())

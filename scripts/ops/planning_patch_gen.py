#!/usr/bin/env python3
"""
Generate episode-scoped Planning Patch YAML files in bulk.

Why:
- Keep the operational unit as "1 patch = 1 episode" (safe/auditable),
  while still supporting series/template bulk changes by emitting many patch files.
- Patches are tracked under `workspaces/planning/patches/` and applied via:
  `python3 scripts/ops/planning_apply_patch.py --patch ... [--patch ...] [--apply]`

SSOT:
- ssot/ops/OPS_PLANNING_PATCHES.md
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from _bootstrap import bootstrap

PROJECT_ROOT = bootstrap(load_env=False)

from factory_common.locks import default_active_locks_for_mutation, find_blocking_lock
from factory_common.paths import planning_root, repo_root


def _utc_today_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


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


def _sanitize_label(label: str) -> str:
    raw = (label or "").strip()
    if not raw:
        return ""
    # Keep filenames predictable and shell-safe.
    s = re.sub(r"\s+", "_", raw)
    s = re.sub(r"[^A-Za-z0-9_.-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("._-")
    return s


def _parse_set_items(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in items:
        s = str(raw or "")
        if "=" not in s:
            raise ValueError(f"Invalid --set item (expected COL=VALUE): {raw!r}")
        col, value = s.split("=", 1)
        col = col.strip()
        if not col:
            raise ValueError(f"Invalid --set item (empty column): {raw!r}")
        out[col] = value
    if not out:
        raise ValueError("--set must be provided at least once")
    return out


def _expand_videos(*, videos: list[str] | None, start: int | None, end: int | None) -> list[str]:
    out: list[str] = []
    if videos:
        for v in videos:
            out.append(_normalize_video(str(v)))
    elif start is not None or end is not None:
        if start is None or end is None:
            raise ValueError("Both --from and --to are required when using range mode")
        if end < start:
            raise ValueError("--to must be >= --from")
        for n in range(start, end + 1):
            out.append(_normalize_video(str(n)))
    else:
        raise ValueError("Provide either --videos or --from/--to")

    # de-dup while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for v in out:
        if v in seen:
            continue
        seen.add(v)
        deduped.append(v)
    return deduped


def _resolve_out_dir(value: str | None) -> Path:
    if not value:
        return planning_root() / "patches"
    p = Path(value).expanduser()
    if p.is_absolute():
        return p
    return repo_root() / p


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--op", choices=["set", "add_row"], required=True, help="Patch apply op")
    ap.add_argument("--channel", required=True, help="Channel code like CH07")
    ap.add_argument("--videos", nargs="*", help="Video numbers (e.g. 1 2 3 or 001 002 003)")
    ap.add_argument("--from", dest="from_video", type=int, help="Range start (inclusive)")
    ap.add_argument("--to", dest="to_video", type=int, help="Range end (inclusive)")
    ap.add_argument("--set", action="append", default=[], help="Column update, COL=VALUE (repeatable)")
    ap.add_argument("--label", required=True, help="Patch label (used in filename/patch_id)")
    ap.add_argument("--notes", default="", help="Optional notes text")
    ap.add_argument("--out-dir", help="Output dir (default: workspaces/planning/patches)")
    ap.add_argument("--write", action="store_true", help="Write patch YAML files (default: stdout only)")
    ap.add_argument("--overwrite", action="store_true", help="Overwrite existing patch files (use with care)")
    ap.add_argument("--ignore-locks", action="store_true", help="Ignore coordination locks (use with caution)")
    args = ap.parse_args(argv)

    channel = _normalize_channel(args.channel)
    if not re.fullmatch(r"CH\d{2}", channel):
        print(f"ERROR: invalid --channel: {args.channel!r}", file=sys.stderr)
        return 2

    label = _sanitize_label(args.label)
    if not label:
        print("ERROR: --label becomes empty after sanitization; use a simpler ASCII label", file=sys.stderr)
        return 2

    try:
        values = _parse_set_items(list(args.set or []))
        targets = _expand_videos(videos=list(args.videos or []), start=args.from_video, end=args.to_video)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    out_dir = _resolve_out_dir(args.out_dir)
    if not args.ignore_locks:
        locks = default_active_locks_for_mutation()
        blocking = find_blocking_lock(out_dir, locks)
        if blocking is not None:
            print(
                f"ERROR: output dir is locked: {out_dir} (lock_id={blocking.lock_id}, mode={blocking.mode}, by={blocking.created_by})",
                file=sys.stderr,
            )
            return 2

    stamp = _utc_today_compact()

    emitted: list[tuple[Path, str]] = []
    for video in targets:
        patch_id = f"{channel}-{video}__{label}_{stamp}"
        filename = f"{channel}-{video}__{label}.yaml"
        path = out_dir / filename

        if not args.ignore_locks:
            locks = default_active_locks_for_mutation()
            blocking = find_blocking_lock(path, locks)
            if blocking is not None:
                print(
                    f"ERROR: output path is locked: {path} (lock_id={blocking.lock_id}, mode={blocking.mode}, by={blocking.created_by})",
                    file=sys.stderr,
                )
                return 2

        payload = {
            "schema": "ytm.planning_patch.v1",
            "patch_id": patch_id,
            "target": {"channel": channel, "video": video},
            "apply": {args.op: values},
            "notes": str(args.notes or ""),
        }

        yaml_text = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
        emitted.append((path, yaml_text))

    if not args.write:
        for path, yaml_text in emitted:
            print(f"# --- {path} ---")
            print(yaml_text.rstrip() + "\n")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    for path, yaml_text in emitted:
        if path.exists() and not args.overwrite:
            print(f"ERROR: already exists: {path} (use --overwrite or change --label)", file=sys.stderr)
            return 2
        path.write_text(yaml_text, encoding="utf-8")
        print(f"Wrote: {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))


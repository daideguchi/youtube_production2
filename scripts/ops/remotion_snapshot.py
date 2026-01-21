#!/usr/bin/env python3
"""
remotion_snapshot.py — Remotion snapshot helper (frame PNG) for layout QC.

SSOT:
  - ssot/plans/PLAN_REMOTION_MAINLINE.md
  - ssot/ops/OPS_ENTRYPOINTS_INDEX.md

Policy:
  - default is dry-run (no writes). Add --run to render.
  - missing images => stop by default (pass --allow-missing-images for debug only).
  - preflight requires minimal run_dir contract: image_cues.json + belt_config.json.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path

from _bootstrap import bootstrap

bootstrap(load_env=False)

from factory_common import paths as repo_paths  # noqa: E402
from factory_common.locks import default_active_locks_for_mutation, find_blocking_lock  # noqa: E402


REPO_ROOT = repo_paths.repo_root()


def _rel(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except Exception:
        return str(path)


def _z3(value: int | str) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if not digits:
        raise SystemExit(f"Invalid video: {value!r}")
    return f"{int(digits):03d}"


def _norm_channel(raw: str) -> str:
    s = str(raw or "").strip().upper()
    m = re.fullmatch(r"CH(\d{1,3})", s)
    if m:
        return f"CH{int(m.group(1)):02d}"
    if re.fullmatch(r"CH\d{2}", s):
        return s
    raise SystemExit(f"Invalid --channel: {raw!r} (expected CHxx)")


def _resolve_run_id(*, channel: str, video: str, run_suffix: str, explicit_run_id: str | None) -> str:
    rid = str(explicit_run_id or "").strip()
    if rid:
        return rid
    return f"{channel}-{video}{str(run_suffix or '')}"


def _default_out_path(*, run_dir: Path, frame: int) -> Path:
    # Keep snapshots under run_dir so they are episode-scoped and easy to diff.
    return run_dir / "remotion" / "snapshots" / f"snapshot_f{int(frame)}.png"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render a single Remotion snapshot frame (PNG) (dry-run default)")
    ap.add_argument("--channel", required=True, help="CHxx")
    ap.add_argument("--video", required=True, help="NNN")
    ap.add_argument("--run-suffix", default="_capcut_v1", help="default: _capcut_v1 (used when --run-id not set)")
    ap.add_argument("--run-id", default="", help="explicit run_id (overrides --run-suffix)")
    ap.add_argument("--frame", type=int, default=300, help="frame number (default: 300)")
    ap.add_argument("--out", default="", help="optional output png path (absolute or repo-relative)")
    ap.add_argument("--allow-missing-images", action="store_true", help="Allow missing images (debug only; default stops)")
    ap.add_argument("--check-remote", action="store_true", help="Check remote image URLs via HEAD/GET before render")
    ap.add_argument("--remote-timeout-ms", type=int, default=4000)
    ap.add_argument("--remote-retries", type=int, default=2)
    ap.add_argument("--run", action="store_true", help="execute (default: dry-run)")
    ap.add_argument("--dry-run", action="store_true", help="explicit dry-run (overrides --run)")
    ap.add_argument("--ignore-locks", action="store_true", help="ignore coordination locks (debug only)")
    args = ap.parse_args(argv)

    if not bool(args.run):
        args.dry_run = True

    channel = _norm_channel(args.channel)
    video = _z3(args.video)
    run_id = _resolve_run_id(channel=channel, video=video, run_suffix=str(args.run_suffix), explicit_run_id=args.run_id)
    run_dir = repo_paths.video_run_dir(run_id)
    if not run_dir.exists():
        raise SystemExit(f"[MISSING] run_dir: {_rel(run_dir)}")

    required = [run_dir / "image_cues.json", run_dir / "belt_config.json"]
    missing = [p for p in required if not p.exists()]
    if missing:
        raise SystemExit("[MISSING] run_dir inputs: " + ",".join([_rel(p) for p in missing]))

    out_path: Path
    if str(args.out or "").strip():
        p = Path(str(args.out)).expanduser()
        out_path = p if p.is_absolute() else (REPO_ROOT / p).resolve()
    else:
        out_path = _default_out_path(run_dir=run_dir, frame=int(args.frame))

    if not bool(args.ignore_locks):
        lock = find_blocking_lock(out_path, default_active_locks_for_mutation())
        if lock is not None:
            raise SystemExit(f"[LOCKED] Refusing to write snapshot under active lock: {_rel(out_path)} ({lock.lock_id})")

    cmd: list[str] = [
        "node",
        str((REPO_ROOT / "apps" / "remotion" / "scripts" / "snapshot.js").resolve()),
        "--run",
        _rel(run_dir),
        "--channel",
        channel,
        "--frame",
        str(int(args.frame)),
        "--out",
        _rel(out_path) if not out_path.is_absolute() else str(out_path),
        "--remote-timeout-ms",
        str(int(args.remote_timeout_ms)),
        "--remote-retries",
        str(int(args.remote_retries)),
    ]
    if bool(args.check_remote):
        cmd.append("--check-remote")
    if not bool(args.allow_missing_images):
        cmd.append("--fail-on-missing")

    print(f"[remotion_snapshot] run_id={run_id}")
    print(f"[remotion_snapshot] out={_rel(out_path)}")
    print(f"[remotion_snapshot] $ {' '.join(cmd)}", flush=True)

    if bool(args.dry_run):
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), env=dict(os.environ), check=False)
    return int(proc.returncode)


if __name__ == "__main__":
    raise SystemExit(main())


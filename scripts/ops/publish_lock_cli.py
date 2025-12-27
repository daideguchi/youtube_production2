#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from _bootstrap import bootstrap

bootstrap(load_env=True)

from factory_common.publish_lock import (  # noqa: E402
    mark_episode_published_locked,
    unmark_episode_published_locked,
)


def _print_json(payload) -> None:
    try:
        print(json.dumps(asdict(payload), ensure_ascii=False, indent=2))
    except Exception:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_lock(args: argparse.Namespace) -> int:
    res = mark_episode_published_locked(
        args.channel,
        args.video,
        force_complete=not args.no_force_complete,
        published_at=args.published_at,
        update_status_json=not args.no_status_json,
    )
    _print_json(res)
    return 0


def cmd_unlock(args: argparse.Namespace) -> int:
    res = unmark_episode_published_locked(
        args.channel,
        args.video,
        restore_progress=args.restore_progress,
        update_status_json=not args.no_status_json,
    )
    _print_json(res)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Operator CLI for published_lock (planning CSV + status.json).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    lock = sub.add_parser("lock", help="Mark episode as published (sets progress to 投稿済み + status.json guard).")
    lock.add_argument("--channel", required=True, help="Channel code (e.g. CH02)")
    lock.add_argument("--video", required=True, help="Video number (e.g. 024)")
    lock.add_argument("--published-at", help="YYYY-MM-DD (optional; default: today UTC)")
    lock.add_argument(
        "--no-force-complete",
        action="store_true",
        help="Do not force-fill completion fields when marking as published.",
    )
    lock.add_argument("--no-status-json", action="store_true", help="Do not write status.json metadata.")
    lock.set_defaults(func=cmd_lock)

    unlock = sub.add_parser("unlock", help="Clear published_lock when it was set by mistake.")
    unlock.add_argument("--channel", required=True, help="Channel code (e.g. CH02)")
    unlock.add_argument("--video", required=True, help="Video number (e.g. 024)")
    unlock.add_argument(
        "--restore-progress",
        help='Set progress to this value instead of empty when clearing 投稿済み/公開済み (e.g. "script: drafted").',
    )
    unlock.add_argument("--no-status-json", action="store_true", help="Do not write status.json metadata.")
    unlock.set_defaults(func=cmd_unlock)

    args = ap.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())


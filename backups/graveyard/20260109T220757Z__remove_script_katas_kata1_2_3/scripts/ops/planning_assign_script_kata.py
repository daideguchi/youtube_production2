#!/usr/bin/env python3
"""
Assign "script kata" (3 structures) into Planning SoT CSV rows.

SoT:
- Planning CSV: workspaces/planning/channels/CHxx.csv
- Column: 台本型
- Values: kata1 | kata2 | kata3

Design:
- Default assignment is *stable pseudo-random* (hash of script_id + seed),
  so re-running does not churn existing rows.
- By default we only fill missing values; use --overwrite to replace.

Usage:
  python scripts/ops/planning_assign_script_kata.py --channel CH01 --apply
  python scripts/ops/planning_assign_script_kata.py --channel CH01 --apply --overwrite
  python scripts/ops/planning_assign_script_kata.py --all --apply
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from _bootstrap import bootstrap

bootstrap(load_env=False)

from factory_common.paths import channels_csv_path, repo_root  # noqa: E402


KATA_COLUMN = "台本型"
KATAS: tuple[str, str, str] = ("kata1", "kata2", "kata3")


def _normalize_channel(ch: str) -> str:
    s = (ch or "").strip().upper()
    if re.fullmatch(r"CH\d{2}", s):
        return s
    m = re.fullmatch(r"CH(\d+)", s)
    if m:
        return f"CH{int(m.group(1)):02d}"
    return s


def _video_number_from_row(row: dict[str, str]) -> str:
    for key in ("動画番号", "No.", "VideoNumber", "video_number", "video"):
        raw = (row.get(key) or "").strip()
        if not raw:
            continue
        try:
            return f"{int(raw):03d}"
        except Exception:
            return raw
    for key in ("動画ID", "台本番号", "ScriptID", "script_id"):
        v = (row.get(key) or "").strip()
        m = re.search(r"\bCH\d{2}-(\d{3})\b", v)
        if m:
            return m.group(1)
    return ""


def _script_id_from_row(channel: str, row: dict[str, str]) -> str:
    for key in ("動画ID", "台本番号", "ScriptID", "script_id"):
        v = (row.get(key) or "").strip()
        if v:
            return v
    video = _video_number_from_row(row)
    if video:
        return f"{channel}-{video}"
    return ""


def _stable_assign(script_id: str, *, seed: str) -> str:
    payload = f"{seed}:{script_id}".encode("utf-8")
    h = hashlib.sha1(payload).hexdigest()
    idx = int(h[:8], 16) % len(KATAS)
    return KATAS[idx]


@dataclass(frozen=True)
class AssignStats:
    channel: str
    total_rows: int
    touched_rows: int
    filled_rows: int
    counts: dict[str, int]


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows: list[dict[str, str]] = []
        for row in reader:
            rows.append({k: (v if v is not None else "") for k, v in row.items()})
    return fieldnames, rows


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})
    tmp.replace(path)


def assign_channel_csv(
    channel: str,
    *,
    seed: str,
    overwrite: bool,
    apply: bool,
) -> AssignStats:
    channel = _normalize_channel(channel)
    path = channels_csv_path(channel)
    if not path.exists():
        raise SystemExit(f"Planning CSV not found: {path}")

    fieldnames, rows = _read_csv(path)
    if KATA_COLUMN not in fieldnames:
        fieldnames.append(KATA_COLUMN)

    counts: dict[str, int] = {k: 0 for k in KATAS}
    touched = 0
    filled = 0

    for row in rows:
        raw_title = (row.get("タイトル") or "").strip()
        if not raw_title:
            continue
        script_id = _script_id_from_row(channel, row)
        if not script_id:
            continue

        current = (row.get(KATA_COLUMN) or "").strip()
        if current and not overwrite:
            # Keep existing assignment stable.
            if current in counts:
                counts[current] += 1
            continue

        kata = _stable_assign(script_id, seed=seed)
        row[KATA_COLUMN] = kata
        counts[kata] += 1
        touched += 1
        if not current:
            filled += 1

    if apply:
        _write_csv(path, fieldnames, rows)

    return AssignStats(
        channel=channel,
        total_rows=len(rows),
        touched_rows=touched,
        filled_rows=filled,
        counts=counts,
    )


def _iter_channels_from_args(args: argparse.Namespace) -> Iterable[str]:
    if args.all:
        root = repo_root() / "workspaces" / "planning" / "channels"
        if not root.exists():
            return []
        out: list[str] = []
        for p in sorted(root.glob("CH*.csv")):
            out.append(_normalize_channel(p.stem))
        return out
    out = [_normalize_channel(ch) for ch in (args.channel or [])]
    return [ch for ch in out if ch]


def main() -> int:
    ap = argparse.ArgumentParser(description="Assign Planning CSV column 台本型 with kata1/kata2/kata3.")
    ap.add_argument("--channel", action="append", help="Channel code (repeatable). e.g. CH01")
    ap.add_argument("--all", action="store_true", help="Apply to all CH*.csv under workspaces/planning/channels/")
    ap.add_argument("--seed", default="kata_v1", help="Stable pseudo-random seed (default: kata_v1)")
    ap.add_argument("--overwrite", action="store_true", help="Overwrite existing 台本型 values")
    ap.add_argument("--apply", action="store_true", help="Write changes to CSV (default: dry-run)")
    args = ap.parse_args()

    channels = list(_iter_channels_from_args(args))
    if not channels:
        ap.error("Specify --channel CHxx (repeatable) or --all")

    for ch in channels:
        stats = assign_channel_csv(ch, seed=str(args.seed), overwrite=bool(args.overwrite), apply=bool(args.apply))
        mode = "APPLY" if args.apply else "DRY"
        dist = ", ".join([f"{k}={stats.counts.get(k, 0)}" for k in KATAS])
        print(
            f"[{mode}] {stats.channel}: rows={stats.total_rows} touched={stats.touched_rows} filled={stats.filled_rows} {dist}"
        )

    if not args.apply:
        print("Dry-run only. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


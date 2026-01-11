#!/usr/bin/env python3
"""
workspace_snapshot — workspaces/ の容量スナップショット（観測用 / 安全）

目的:
- 「どこが肥大化しているか」を観測値として即座に把握し、cleanup の優先順位を迷わず決める
- cleanup 実行前後の差分確認（容量/探索ノイズ）を容易にする

安全:
- 削除/移動は一切しない（read-only）
- report は `workspaces/logs/regression/workspace_snapshot/` に JSON で保存できる

SSOT:
- `ssot/plans/PLAN_OPS_STORAGE_LIGHTWEIGHT.md`
- `ssot/plans/PLAN_OPS_ARTIFACT_LIFECYCLE.md`

注意:
- サイズ計測は `du` を優先（高速）。`du` が無い場合のみ Python でフォールバック（遅い）。
- 1回の実行でも `workspaces/video` などは数十GBスキャンするため、環境によっては時間がかかる。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from _bootstrap import bootstrap

bootstrap(load_env=False)

from factory_common.paths import logs_root, repo_root, workspace_root  # noqa: E402


REPORT_SCHEMA = "ytm.workspace_snapshot.v1"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _human_bytes(num_bytes: int) -> str:
    n = float(max(0, int(num_bytes)))
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    i = 0
    while n >= 1024.0 and i < len(units) - 1:
        n /= 1024.0
        i += 1
    if i <= 1:
        return f"{int(n)} {units[i]}"
    return f"{n:.2f} {units[i]}"


def _to_repo_rel(p: Path) -> str:
    root = repo_root()
    try:
        return p.resolve().relative_to(root).as_posix()
    except Exception:
        return str(p)


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(repo_root()),
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def _parse_du_lines(stdout: str) -> dict[str, int]:
    """
    Parse du output like:
      <kb> <path>
    and return {path_str: kb}.
    """
    out: dict[str, int] = {}
    for raw in (stdout or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            kb_s, path_s = line.split(None, 1)
            kb = int(kb_s)
        except Exception:
            continue
        out[path_s.strip()] = kb
    return out


def _du_kb(path: Path) -> tuple[int, str, Optional[str]]:
    """
    Return (kb, backend, error).
    backend: 'du' or 'python'
    """
    rel = _to_repo_rel(path)
    if rel == str(path):
        rel = path.as_posix()

    p = _run(["du", "-sk", rel])
    if p.returncode == 0 and p.stdout:
        parsed = _parse_du_lines(p.stdout)
        if parsed:
            kb = next(iter(parsed.values()))
            return kb, "du", None

    # Fallback: Python walk (slower)
    total = 0
    try:
        if path.is_file():
            total = int(path.stat().st_size)
        else:
            for dirpath, _dirnames, filenames in os.walk(path, followlinks=False):
                dp = Path(dirpath)
                for name in filenames:
                    fp = dp / name
                    try:
                        st = fp.lstat()
                    except Exception:
                        continue
                    total += int(st.st_size)
        return (total + 1023) // 1024, "python", f"du_failed rc={p.returncode}"
    except Exception as exc:
        return 0, "python", f"du_failed rc={p.returncode}; python_failed: {exc}"


def _du_depth1_kb(path: Path) -> tuple[dict[str, int], str, Optional[str]]:
    """
    Return ({path_str: kb}, backend, error) for depth=1 du.
    Tries BSD du first (-d 1), then GNU du (--max-depth=1).
    """
    rel = _to_repo_rel(path)
    if rel == str(path):
        rel = path.as_posix()

    # BSD (macOS): du -d 1
    # NOTE: macOS du treats (-s) and (-d) as mutually exclusive, so do NOT combine them.
    p = _run(["du", "-k", "-d", "1", rel])
    if p.returncode == 0 and p.stdout:
        return _parse_du_lines(p.stdout), "du_bsd", None

    # GNU: du --max-depth=1
    p2 = _run(["du", "-k", "--max-depth=1", rel])
    if p2.returncode == 0 and p2.stdout:
        return _parse_du_lines(p2.stdout), "du_gnu", None

    return {}, "du", f"depth1 du failed (bsd_rc={p.returncode} gnu_rc={p2.returncode})"


@dataclass(frozen=True)
class Item:
    path: str
    bytes: int
    source: str
    error: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "bytes": int(self.bytes),
            "human": _human_bytes(int(self.bytes)),
            "source": self.source,
            "error": self.error,
        }


def _write_report(payload: dict[str, Any]) -> Path:
    out_dir = logs_root() / "regression" / "workspace_snapshot"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"workspace_snapshot_{_utc_now_compact()}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def _sorted_items(items: list[Item]) -> list[Item]:
    return sorted(items, key=lambda x: x.bytes, reverse=True)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Snapshot workspaces sizes (safe; no delete).")
    ap.add_argument(
        "--breakdown",
        default="video,scripts",
        help="Comma-separated top-level dirs to breakdown (depth=1 du). default: video,scripts",
    )
    ap.add_argument("--max-print", type=int, default=20, help="Max rows to print per section (default: 20).")
    ap.add_argument("--json", action="store_true", help="Emit JSON to stdout instead of pretty text.")
    ap.add_argument("--write-report", action="store_true", help="Write JSON report under logs_root()/regression/workspace_snapshot/")
    args = ap.parse_args(argv)

    ws_root = workspace_root()
    breakdown = {s.strip() for s in str(args.breakdown).split(",") if s.strip()}
    max_print = max(0, int(args.max_print))

    top_dirs: list[Path] = []
    top_files_bytes = 0
    if ws_root.exists():
        for p in sorted(ws_root.iterdir(), key=lambda x: x.name):
            if p.is_dir():
                top_dirs.append(p)
            elif p.is_file():
                try:
                    top_files_bytes += int(p.stat().st_size)
                except Exception:
                    pass

    totals: list[Item] = []
    breakdowns: dict[str, list[Item]] = {}

    for d in top_dirs:
        if d.name in breakdown:
            mapping, backend, err = _du_depth1_kb(d)
            # The du output includes the parent itself and children.
            parent_rel = _to_repo_rel(d)
            parent_kb = mapping.get(parent_rel, 0)
            totals.append(Item(path=parent_rel, bytes=int(parent_kb) * 1024, source=backend, error=err))

            children: list[Item] = []
            for p_str, kb in mapping.items():
                if p_str == parent_rel:
                    continue
                children.append(Item(path=p_str, bytes=int(kb) * 1024, source=backend, error=None))
            breakdowns[parent_rel] = _sorted_items(children)
            continue

        kb, backend, err = _du_kb(d)
        totals.append(Item(path=_to_repo_rel(d), bytes=int(kb) * 1024, source=backend, error=err))

    totals_sorted = _sorted_items(totals)
    total_bytes_sum = sum(i.bytes for i in totals_sorted) + int(top_files_bytes)

    payload: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "created_at": _utc_now_iso(),
        "repo_root": str(repo_root()),
        "workspaces_root": str(ws_root),
        "breakdown": sorted(breakdown),
        "totals": [i.as_dict() for i in totals_sorted],
        "breakdowns": {k: [i.as_dict() for i in v] for k, v in breakdowns.items()},
        "workspaces_root_files_bytes": int(top_files_bytes),
        "workspaces_total_bytes_approx": int(total_bytes_sum),
        "workspaces_total_human_approx": _human_bytes(int(total_bytes_sum)),
        "note": "workspaces_total_bytes_approx is sum(top-level dirs) + root files (du not run on whole tree).",
    }

    if args.write_report:
        report_path = _write_report(payload)
        payload["report_path"] = _to_repo_rel(report_path)

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    # Pretty output
    print(f"[workspace_snapshot] created_at={payload['created_at']}")
    print(f"[workspace_snapshot] workspaces_root={_to_repo_rel(ws_root)}")
    print(f"[workspace_snapshot] total_approx={payload['workspaces_total_human_approx']}")
    if args.write_report:
        print(f"[workspace_snapshot] report={payload.get('report_path')}")

    print("\nTop-level:")
    for i, it in enumerate(totals_sorted[:max_print], start=1):
        print(f"- {it.as_dict()['human']:>10}  {it.path}")
    if max_print and len(totals_sorted) > max_print:
        print(f"... ({len(totals_sorted) - max_print} more)")

    for root_path, items in breakdowns.items():
        print(f"\nBreakdown: {root_path}")
        for i, it in enumerate(items[:max_print], start=1):
            print(f"- {it.as_dict()['human']:>10}  {it.path}")
        if max_print and len(items) > max_print:
            print(f"... ({len(items) - max_print} more)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

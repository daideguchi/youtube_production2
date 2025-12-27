#!/usr/bin/env python3
"""
offload_archives_to_external — Offload archived artifacts to external storage.

Purpose:
  - Reduce local disk usage / exploration noise by moving already-archived artifacts to an external SSD.
  - Keep the repo structure intact by mirroring repo-relative paths under an external root.

Scope (default targets):
  - workspaces/**/_archive/**
  - backups/graveyard/**

Safety:
  - Dry-run by default; pass --run to execute.
  - --mode move deletes local only after a successful copy + verification.

External root:
  - CLI: --external-root /path/to/ssd_root
  - Env: YTM_OFFLOAD_ROOT=/path/to/ssd_root
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from _bootstrap import bootstrap


bootstrap(load_env=True)

from factory_common import paths as fc_paths  # noqa: E402


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _human_bytes(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    for unit in ["KiB", "MiB", "GiB", "TiB"]:
        n_f = n / 1024.0
        if n_f < 1024.0:
            return f"{n_f:.1f}{unit}"
        n = int(n_f)
    return f"{n}B"


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def _rsync_available() -> bool:
    return shutil.which("rsync") is not None


def _run_rsync_copy(*, src: Path, dest: Path, follow_symlinks: bool) -> tuple[int, str, str]:
    """
    Copy src -> dest using rsync.
    - src: file or directory
    - dest: file or directory (for directory sync, dest should be the directory representing src)
    """
    if src.is_dir():
        dest.mkdir(parents=True, exist_ok=True)
        args = ["rsync", "-rlt", "--human-readable", "--stats"]
        if follow_symlinks:
            args.append("-L")
        else:
            args.append("--links")
        args += [str(src) + "/", str(dest) + "/"]
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        args = ["rsync", "-t"]
        if follow_symlinks:
            args.append("-L")
        else:
            args.append("--links")
        args += [str(src), str(dest)]

    proc = subprocess.run(args, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return proc.returncode, proc.stdout, proc.stderr


def _run_rsync_verify(*, src: Path, dest: Path, follow_symlinks: bool) -> tuple[bool, list[str]]:
    """
    Verify that dest contains src (size-based). Returns (ok, diff_lines).
    We ignore directory-only changes (metadata) and focus on file content presence.
    """
    if src.is_dir():
        args = ["rsync", "-rlt", "--dry-run", "--size-only", "--itemize-changes", "--out-format=%i %n%L"]
        if follow_symlinks:
            args.append("-L")
        else:
            args.append("--links")
        args += [str(src) + "/", str(dest) + "/"]
    else:
        args = ["rsync", "-t", "--dry-run", "--size-only", "--itemize-changes", "--out-format=%i %n%L"]
        if follow_symlinks:
            args.append("-L")
        else:
            args.append("--links")
        args += [str(src), str(dest)]

    proc = subprocess.run(args, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if proc.returncode not in (0, 1):
        # Treat verification failures as not OK.
        lines = [x for x in (proc.stderr or "").splitlines() if x.strip()]
        if not lines:
            lines = [x for x in (proc.stdout or "").splitlines() if x.strip()]
        return False, lines or ["rsync verify failed"]

    diff_lines: list[str] = []
    for raw in (proc.stdout or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        # Ignore non-itemize noise (rare), only keep itemize lines that start with one of: < > c . h *
        head = line.split(" ", 1)[0] if " " in line else line
        if not head or head[0] not in {"<", ">", "c", ".", "h", "*"}:
            continue
        # Ignore directory metadata differences: itemize format's 2nd char is file type (d=dir, f=file, L=symlink)
        if len(head) >= 2 and head[1] == "d":
            continue
        diff_lines.append(line)

    return len(diff_lines) == 0, diff_lines


def _safe_remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _estimate_size_bytes(path: Path) -> int:
    try:
        if path.is_symlink():
            return 0
        if path.is_file():
            return path.stat().st_size
        if not path.is_dir():
            return 0
    except FileNotFoundError:
        return 0

    # Fast path: prefer `du` to avoid slow Python-level full tree walks on large archives.
    try:
        proc = subprocess.run(
            ["du", "-sk", str(path)],
            cwd=str(path.parent),
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            kb = int(proc.stdout.strip().split()[0])
            return kb * 1024
    except Exception:
        pass
    return 0


@dataclass(frozen=True)
class Candidate:
    src: Path
    rel: str
    approx_size_bytes: int


def _default_collection_roots(repo_root: Path) -> list[Path]:
    ws = fc_paths.workspace_root()
    return [
        repo_root / "backups" / "graveyard",
        ws / "scripts" / "_archive",
        ws / "video" / "_archive",
        ws / "video" / "_capcut_drafts" / "_archive",
        ws / "thumbnails" / "_archive",
    ]


def _collect_candidates(*, repo_root: Path, roots: Iterable[Path], min_age_days: int) -> list[Candidate]:
    now = datetime.now(timezone.utc).timestamp()
    out: list[Candidate] = []
    for root in roots:
        if not root.exists():
            continue
        if not _is_within(root, repo_root):
            continue
        if not root.is_dir():
            continue

        for entry in sorted(root.iterdir(), key=lambda p: p.name):
            try:
                st = entry.lstat()
            except FileNotFoundError:
                continue
            age_days = int((now - st.st_mtime) // (60 * 60 * 24))
            if age_days < min_age_days:
                continue
            try:
                rel = entry.relative_to(repo_root).as_posix()
            except Exception:
                continue
            size_b = _estimate_size_bytes(entry)
            out.append(Candidate(src=entry, rel=rel, approx_size_bytes=size_b))
    return out


def _write_report(*, report_path: Path, payload: dict) -> None:
    import json

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Offload archived artifacts to external SSD (dry-run by default).")
    ap.add_argument("--external-root", help="External root directory (or set YTM_OFFLOAD_ROOT).")
    ap.add_argument(
        "--root",
        action="append",
        help="Repo-relative root directory to collect from (repeatable). If omitted, uses the default archive roots.",
    )
    ap.add_argument("--min-age-days", type=int, default=0, help="Only offload entries older than N days (by mtime).")
    ap.add_argument("--mode", choices=["copy", "move"], default="copy", help="copy: keep local; move: delete local after verified copy.")
    ap.add_argument("--follow-symlinks", action="store_true", help="Copy symlink targets instead of preserving symlinks (-L).")
    ap.add_argument("--limit", type=int, default=0, help="Limit number of candidates (0=unlimited).")
    ap.add_argument("--run", action="store_true", help="Execute (otherwise dry-run).")
    args = ap.parse_args()

    repo_root = fc_paths.repo_root()

    external_root_raw = args.external_root or os.getenv("YTM_OFFLOAD_ROOT") or os.getenv("FACTORY_OFFLOAD_ROOT")
    if not external_root_raw:
        raise SystemExit("external root not set (use --external-root or set YTM_OFFLOAD_ROOT)")
    external_root = Path(external_root_raw).expanduser().resolve()
    if not external_root.exists():
        raise SystemExit(f"external root not found: {external_root}")
    if not external_root.is_dir():
        raise SystemExit(f"external root is not a directory: {external_root}")
    if _is_within(external_root, repo_root):
        raise SystemExit(f"external root must be outside repo: {external_root}")

    if args.root:
        roots: list[Path] = []
        for raw in args.root:
            p = Path(str(raw)).expanduser()
            if not p.is_absolute():
                p = repo_root / p
            roots.append(p.resolve())
    else:
        roots = _default_collection_roots(repo_root)
    candidates = _collect_candidates(repo_root=repo_root, roots=roots, min_age_days=max(0, int(args.min_age_days)))
    if args.limit and args.limit > 0:
        candidates = candidates[: int(args.limit)]

    if args.mode == "move" and not _rsync_available():
        raise SystemExit("--mode move requires rsync (not found on PATH)")

    total_b = sum(c.approx_size_bytes for c in candidates)
    print(
        f"[offload_archives_to_external] mode={'run' if args.run else 'dry-run'} action={args.mode} candidates={len(candidates)} approx={_human_bytes(total_b)}"
    )

    ts = _utc_now_compact()
    log_dir = fc_paths.logs_root() / "regression" / "offload_archives_to_external"
    report_path = log_dir / f"offload_report_{ts}.json"

    results: list[dict] = []
    errors: list[str] = []
    verified_failures: list[str] = []
    skipped: list[str] = []

    if not candidates:
        payload = {
            "schema": "ytm.external_offload_report.v1",
            "generated_at": _utc_now_iso(),
            "mode": "run" if args.run else "dry-run",
            "action": args.mode,
            "external_root": str(external_root),
            "min_age_days": int(args.min_age_days),
            "follow_symlinks": bool(args.follow_symlinks),
            "candidates": [],
            "results": [],
            "skipped": [],
            "errors": [],
            "verified_failures": [],
        }
        _write_report(report_path=report_path, payload=payload)
        print(f"[offload_archives_to_external] nothing to do report={report_path}")
        return 0

    if args.mode == "move" and not args.run:
        print("[offload_archives_to_external] NOTE: --mode move requested but dry-run; nothing will be deleted.")

    if args.run and args.mode == "move":
        print("[offload_archives_to_external] move: local paths will be deleted only after a verified copy.")

    for c in candidates:
        src = c.src
        if not src.exists():
            skipped.append(f"missing: {c.rel}")
            continue
        dest = external_root / c.rel

        item = {
            "rel": c.rel,
            "src": str(src),
            "dest": str(dest),
            "approx_size_bytes": int(c.approx_size_bytes),
            "status": "planned" if args.run else "dry-run",
            "copy": None,
            "verify": None,
            "delete_local": None,
        }

        if not args.run:
            results.append(item)
            continue

        try:
            if _rsync_available():
                rc, out, err = _run_rsync_copy(src=src, dest=dest, follow_symlinks=bool(args.follow_symlinks))
                item["copy"] = {"tool": "rsync", "returncode": rc}
                if rc != 0:
                    item["status"] = "copy_failed"
                    if err:
                        item["copy"]["stderr_tail"] = "\n".join(err.splitlines()[-20:])
                    errors.append(f"{c.rel}: rsync copy failed (rc={rc})")
                    results.append(item)
                    continue
            else:
                if src.is_dir():
                    if dest.exists():
                        shutil.rmtree(dest)
                    shutil.copytree(src, dest, symlinks=not bool(args.follow_symlinks))
                else:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
                item["copy"] = {"tool": "shutil", "returncode": 0}

            if args.mode == "move":
                ok, diffs = _run_rsync_verify(src=src, dest=dest, follow_symlinks=bool(args.follow_symlinks)) if _rsync_available() else (True, [])
                item["verify"] = {"ok": bool(ok), "diffs": diffs[:50]}
                if not ok:
                    item["status"] = "verify_failed"
                    verified_failures.append(f"{c.rel}: verify_failed")
                    results.append(item)
                    continue

                _safe_remove_path(src)
                item["delete_local"] = {"ok": True}
                item["status"] = "moved"
            else:
                item["status"] = "copied"

        except Exception as exc:
            item["status"] = "error"
            item["error"] = str(exc)
            errors.append(f"{c.rel}: {exc}")

        results.append(item)

    payload = {
        "schema": "ytm.external_offload_report.v1",
        "generated_at": _utc_now_iso(),
        "mode": "run" if args.run else "dry-run",
        "action": args.mode,
        "external_root": str(external_root),
        "min_age_days": int(args.min_age_days),
        "follow_symlinks": bool(args.follow_symlinks),
        "counts": {
            "candidates": len(candidates),
            "planned": len([x for x in results if x.get("status") in {"dry-run", "planned"}]),
            "copied": len([x for x in results if x.get("status") == "copied"]),
            "moved": len([x for x in results if x.get("status") == "moved"]),
            "skipped": len(skipped),
            "errors": len(errors),
            "verify_failed": len(verified_failures),
        },
        "candidates": [{"rel": c.rel, "approx_size_bytes": int(c.approx_size_bytes)} for c in candidates],
        "results": results,
        "skipped": skipped,
        "errors": errors,
        "verified_failures": verified_failures,
    }

    _write_report(report_path=report_path, payload=payload)
    print(f"[offload_archives_to_external] report={report_path}")
    return 0 if not errors and not verified_failures else 2


if __name__ == "__main__":
    raise SystemExit(main())

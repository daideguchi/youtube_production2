#!/usr/bin/env python3
"""
workspaces_mirror.py — mirror Mac-local workspaces into the vault (create/update + delete sync).

Contract (explicit; avoid drift):
- When a file appears/changes under local `workspaces/**`, the same path is copied to the vault.
- When a file is deleted locally, it is deleted in the vault too (mirror).
- Planning SSOT is handled separately (shared main branch). By default we do NOT mirror `planning/`
  to avoid overwriting progress that may be updated via the remote UI.

SSOT:
- ssot/ops/OPS_IMAGE_DDD_STORAGE_MAP_AND_APPROVAL.md
- ssot/ops/OPS_SHARED_WORKSPACES_REMOTE_UI.md
- ssot/ops/OPS_ENV_VARS.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from _bootstrap import bootstrap

REPO_ROOT = bootstrap(load_env=True)

from factory_common import paths as repo_paths  # noqa: E402


REPORT_SCHEMA = "ytm.ops.workspaces_mirror.v1"
VAULT_SENTINEL_SCHEMA = "ytm.vault.workspaces_root.v1"
VAULT_SENTINEL_NAME = ".ytm_vault_workspaces_root.json"
MOUNTPOINT_STUB_NAME = "README_MOUNTPOINT.txt"

_RSYNC_HELP_CACHE: dict[str, str] = {}


def _rsync_bin() -> str:
    override = (os.getenv("YTM_RSYNC_BIN") or "").strip()
    if override:
        return override

    for candidate in ("/opt/homebrew/bin/rsync", "/usr/local/bin/rsync"):
        if Path(candidate).exists():
            return candidate

    # Fall back to PATH (typically /usr/bin/rsync).
    return "rsync"


def _env_int(name: str, *, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except ValueError:
        return int(default)


def _env_bool(name: str, *, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in ("1", "true", "yes", "y", "on"):
        return True
    if raw in ("0", "false", "no", "n", "off"):
        return False
    return bool(default)


def _rsync_help(rsync_bin: str) -> str:
    cached = _RSYNC_HELP_CACHE.get(rsync_bin)
    if cached is not None:
        return cached
    try:
        proc = subprocess.run(
            [rsync_bin, "--help"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
        )
    except Exception:
        text = ""
    else:
        text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    _RSYNC_HELP_CACHE[rsync_bin] = text
    return text


def _rsync_supports(rsync_bin: str, flag: str) -> bool:
    return flag in _rsync_help(rsync_bin)


def _now_iso_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _now_compact_utc() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _logs_dir() -> Path:
    return repo_paths.logs_root() / "ops" / "workspaces_mirror"


@contextmanager
def _run_lock(*, enabled: bool) -> Any:
    """
    Prevent concurrent --run executions (launchd StartInterval can overlap).
    For dry-run we allow concurrent reads.
    """
    if not enabled:
        yield
        return

    import fcntl  # POSIX only; OK for Mac/Linux.

    lock_path = _logs_dir() / "workspaces_mirror.lock"
    _ensure_dir(lock_path.parent)

    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            # Another instance is still running.
            print("[workspaces_mirror] already running; skip this tick.")
            raise SystemExit(0)

        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n{_now_iso_utc()}\n".encode("utf-8", errors="replace"))
        yield
    finally:
        try:
            os.close(fd)
        except Exception:
            pass


def _report_path(stamp: str) -> Path:
    return _logs_dir() / f"workspaces_mirror__{stamp}.json"


def _safe_token(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(s)).strip("_") or "step"


def _step_log_paths(*, stamp: str, step_name: str) -> tuple[Path, Path]:
    safe = _safe_token(step_name)
    base = _logs_dir() / f"workspaces_mirror__{stamp}__{safe}"
    return base.with_suffix(".stdout.log"), base.with_suffix(".stderr.log")


def _vault_sentinel_path(dest_root: Path) -> Path:
    return dest_root / VAULT_SENTINEL_NAME


def _write_vault_sentinel(*, dest_root: Path) -> Path:
    payload: dict[str, Any] = {
        "schema": VAULT_SENTINEL_SCHEMA,
        "generated_at": _now_iso_utc(),
        "paths": {"dest_root": str(dest_root), "repo_root": str(REPO_ROOT)},
        "env": {"YTM_VAULT_WORKSPACES_ROOT": os.getenv("YTM_VAULT_WORKSPACES_ROOT") or None},
    }
    p = _vault_sentinel_path(dest_root)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def _require_dir(path: Path, *, label: str) -> None:
    if not path.exists():
        raise SystemExit(f"[MISSING] {label}: {path}")
    if not path.is_dir():
        raise SystemExit(f"[MISSING] {label} (not a directory): {path}")


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _is_smbfs_mounted(mountpoint: Path) -> bool:
    """
    Detect macOS smbfs mounts via `/sbin/mount`.
    We only need a fast, best-effort guard to avoid writing into a local stub.
    """
    try:
        proc = subprocess.run(
            ["/sbin/mount"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
        )
    except Exception:
        return False

    out = proc.stdout or ""
    needle = f" on {mountpoint} "
    for line in out.splitlines():
        if needle in line and "(smbfs," in line:
            return True
    return False


def _is_mountpoint_stub(mountpoint: Path) -> bool:
    """
    When Lenovo/NAS is down we keep a local directory with a marker file so tools
    can fail-fast (instead of hanging on a dead smbfs mount).
    """
    try:
        return (mountpoint / MOUNTPOINT_STUB_NAME).exists()
    except Exception:
        return False


def _rsync_cmd(
    *,
    src_root: Path,
    dest_root: Path,
    delete: bool,
    exclude_planning: bool,
    exclude_paths: list[str],
    extra_excludes: list[str],
    stats: bool,
) -> list[str]:
    rsync_bin = _rsync_bin()
    cmd: list[str] = [rsync_bin, "-a", "--human-readable"]

    # Keep Mac editing responsive:
    # - Prefer whole-file (delta algorithm is wasted for binary assets over SMB, and costs CPU).
    if _env_bool("YTM_RSYNC_WHOLE_FILE", default=True) and _rsync_supports(rsync_bin, "--whole-file"):
        cmd.append("--whole-file")

    # Fail fast if the share is flaky (do not hang forever).
    # NOTE: rsync --contimeout is daemon-only; passing it for local-path mirroring fails.
    timeout_sec = _env_int("YTM_RSYNC_TIMEOUT_SEC", default=60)
    if timeout_sec > 0 and _rsync_supports(rsync_bin, "--timeout"):
        cmd.append(f"--timeout={timeout_sec}")

    # Optional bandwidth cap (KB/s). Keep default unlimited; set env to protect interactive work.
    bwlimit_kbps = _env_int("YTM_RSYNC_BWLIMIT_KBPS", default=0)
    if bwlimit_kbps > 0 and _rsync_supports(rsync_bin, "--bwlimit"):
        cmd.append(f"--bwlimit={bwlimit_kbps}")

    if stats:
        cmd.append("--stats")
    if delete:
        cmd += ["--delete", "--delete-delay"]

    # Minimal noise; we keep a structured report anyway.
    cmd += ["--no-perms", "--no-owner", "--no-group"]

    # Cache/garbage excludes (safe).
    for pat in (
        ".DS_Store",
        "__pycache__/",
        ".pytest_cache/",
        ".mypy_cache/",
        ".ruff_cache/",
    ):
        cmd += ["--exclude", pat]

    if exclude_planning:
        # Anchored to the transfer root.
        cmd += ["--exclude", "/planning/"]

    for path in exclude_paths:
        path = str(path or "").strip().strip("/")
        if not path:
            continue
        cmd += ["--exclude", f"/{path}/"]

    for pat in extra_excludes:
        if str(pat).strip():
            cmd += ["--exclude", str(pat)]

    # Trailing slashes = copy contents.
    cmd += [str(src_root) + "/", str(dest_root) + "/"]
    return cmd


def main() -> int:
    ap = argparse.ArgumentParser(description="Mirror local workspaces to the vault (dry-run by default).")
    ap.add_argument("--run", action="store_true", help="Execute mirror (default: dry-run).")
    ap.add_argument("--src-root", default="", help="Override source workspaces root (default: workspace_root()).")
    ap.add_argument(
        "--dest-root",
        default="",
        help="Override destination vault workspaces root (default: vault_workspaces_root()).",
    )
    ap.add_argument(
        "--no-delete",
        dest="delete",
        action="store_false",
        help="Do NOT delete in destination (default: delete sync ON).",
    )
    ap.set_defaults(delete=True)
    ap.add_argument(
        "--include-planning",
        dest="exclude_planning",
        action="store_false",
        help="Also mirror planning/ (default: exclude planning/ to protect shared progress SSOT).",
    )
    ap.set_defaults(exclude_planning=True)
    ap.add_argument(
        "--bootstrap-dest",
        action="store_true",
        help="Create a sentinel file under dest_root (recommended before enabling delete-sync).",
    )
    ap.add_argument(
        "--allow-unsafe-dest",
        action="store_true",
        help="Allow delete-sync even if dest_root has no sentinel (dangerous; for emergency only).",
    )
    ap.add_argument("--exclude", action="append", default=[], help="Extra rsync --exclude pattern (repeatable).")
    ap.add_argument("--ensure-dirs", action="store_true", help="Create destination directory if missing.")
    ap.add_argument("--stats", action="store_true", help="Include rsync --stats in output/report.")
    args = ap.parse_args()

    with _run_lock(enabled=bool(args.run)):
        return _main_with_lock(args)


def _main_with_lock(args: argparse.Namespace) -> int:
    src_root = Path(str(args.src_root).strip()).expanduser().resolve() if str(args.src_root).strip() else repo_paths.workspace_root()
    dest_root: Path | None
    if str(args.dest_root).strip():
        # IMPORTANT: avoid Path.resolve() on network mounts (SMB/NFS).
        dest_root = Path(str(args.dest_root).strip()).expanduser()
    else:
        dest_root = repo_paths.vault_workspaces_root()

    if dest_root is None:
        raise SystemExit(
            "[POLICY] vault workspaces root is not configured.\n"
            "- set YTM_VAULT_WORKSPACES_ROOT to the shared `ytm_workspaces/` directory\n"
            "- or pass --dest-root <path>"
        )

    _require_dir(src_root, label="src_root")

    # Guardrail: never write into a local stub when shared storage is offline.
    # (This is the root cause of "SSOT drift" when mounts flap.)
    shared_root = repo_paths.shared_storage_root()
    if shared_root is not None and _is_relative_to(dest_root, shared_root) and _is_mountpoint_stub(shared_root):
        print(
            "[workspaces_mirror] SKIP: shared storage is OFFLINE/STUB (marker present).\n"
            f"- marker:     {shared_root / MOUNTPOINT_STUB_NAME}\n"
            f"- dest_root:   {dest_root}\n"
            "- action: mount Lenovo share (launchd: com.doraemon.mount_lenovo_share) and retry."
        )
        raise SystemExit(0)

    if shared_root is not None and _is_relative_to(dest_root, shared_root) and not _is_smbfs_mounted(shared_root):
        print(
            "[workspaces_mirror] SKIP: shared storage is not mounted.\n"
            f"- shared_root: {shared_root}\n"
            f"- dest_root:   {dest_root}\n"
            "- action: mount Lenovo share (launchd: com.doraemon.mount_lenovo_share) and retry."
        )
        raise SystemExit(0)

    if not dest_root.exists() and (bool(args.ensure_dirs) or bool(args.bootstrap_dest)):
        _ensure_dir(dest_root)
    _require_dir(dest_root, label="dest_root")

    # Stamp for all artifacts (report + step logs).
    stamp = _now_compact_utc()

    # Safety: refuse obviously wrong destinations.
    if dest_root == src_root:
        raise SystemExit("[POLICY] dest_root must differ from src_root.")
    if dest_root in src_root.parents:
        raise SystemExit(f"[POLICY] dest_root must not be a parent of src_root: dest={dest_root} src={src_root}")

    sentinel_path = _vault_sentinel_path(dest_root)
    if bool(args.bootstrap_dest):
        _ensure_dir(dest_root)
        p = _write_vault_sentinel(dest_root=dest_root)
        print(f"[workspaces_mirror] wrote vault sentinel: {p}")

    # Safety: if we are going to run delete-sync, require a sentinel to reduce accidents.
    if bool(args.run) and bool(args.delete) and (not sentinel_path.exists()) and (not bool(args.allow_unsafe_dest)):
        raise SystemExit(
            "[POLICY] dest_root has no vault sentinel (refusing delete-sync).\n"
            f"- dest_root: {dest_root}\n"
            f"- expected: {sentinel_path}\n"
            "- action: run once with `--bootstrap-dest` (or use --no-delete / --allow-unsafe-dest)"
        )

    # Policy: scripts + thumbnails/assets + generated images are long-lived. Even when delete-sync is enabled,
    # never delete these in the vault.
    protected_paths = ["scripts", "thumbnails/assets", "video/runs"]
    # Policy: these paths are already "shared via symlink/offload" and can differ per-host.
    # Mirroring them as-is would copy host-specific absolute symlinks into the Vault and break other hosts.
    # Keep Vault's own portable links/structure instead.
    main_extra_excludes = [
        f"/{VAULT_SENTINEL_NAME}",  # must never be deleted by rsync --delete
        "/audio/final",  # CapCut WAV/SRT final is already in the shared offload root
        "/video/input",  # video input is offloaded into the shared archive (Vault has portable links)
        "/thumbnails/assets.symlink_*",  # backup aliases (avoid copying host-specific absolute symlinks)
    ]

    commands: list[dict[str, Any]] = []

    # Main pass (everything except protected roots).
    commands.append(
        {
            "name": "main",
            "cmd": _rsync_cmd(
                src_root=src_root,
                dest_root=dest_root,
                delete=bool(args.delete),
                exclude_planning=bool(args.exclude_planning),
                exclude_paths=protected_paths if bool(args.delete) else [],
                extra_excludes=[str(x) for x in (args.exclude or [])] + main_extra_excludes,
                stats=bool(args.stats),
            ),
            "enabled": True,
        }
    )

    # Protected passes (copy/update only; no delete).
    for rel in protected_paths:
        s = src_root / rel
        d = dest_root / rel
        commands.append(
            {
                "name": f"protected:{rel}",
                "dest_dir": str(d),
                "cmd": _rsync_cmd(
                    src_root=s,
                    dest_root=d,
                    delete=False,
                    exclude_planning=False,
                    exclude_paths=[],
                    extra_excludes=[str(x) for x in (args.exclude or [])],
                    stats=bool(args.stats),
                ),
                "enabled": s.exists() and s.is_dir(),
            }
        )

    # Video input may contain a mix of:
    # - symlinked channel directories (offloaded into the shared archive; host-specific absolute links)
    # - real directories (should be mirrored as data)
    # Main pass excludes `/video/input` entirely to avoid copying host-specific symlinks into the Vault.
    # Here we mirror only the *real* directories (non-symlink) under `video/input/`.
    video_input_src_root = src_root / "video" / "input"
    video_input_dest_root = dest_root / "video" / "input"
    if video_input_src_root.exists() and video_input_src_root.is_dir():
        for child in sorted(video_input_src_root.iterdir()):
            if not child.is_dir():
                continue
            if child.is_symlink():
                continue
            dst = video_input_dest_root / child.name
            commands.append(
                {
                    "name": f"video_input:{child.name}",
                    "dest_dir": str(dst),
                    "cmd": _rsync_cmd(
                        src_root=child,
                        dest_root=dst,
                        delete=False,
                        exclude_planning=False,
                        exclude_paths=[],
                        extra_excludes=[str(x) for x in (args.exclude or [])],
                        stats=bool(args.stats),
                    ),
                    "enabled": True,
                }
            )

    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "generated_at": _now_iso_utc(),
        "run": bool(args.run),
        "stamp": stamp,
        "vault": {
            "sentinel": str(sentinel_path),
            "sentinel_exists": bool(sentinel_path.exists()),
            "bootstrap_dest": bool(args.bootstrap_dest),
        },
        "rsync": {
            "commands": commands,
            "delete": bool(args.delete),
            "exclude_planning": bool(args.exclude_planning),
            "extra_excludes": [str(x) for x in (args.exclude or []) if str(x).strip()],
            "protected_paths": protected_paths,
            "tuning": {
                "nice": _env_int("YTM_MIRROR_NICE", default=10),
                "whole_file": _env_bool("YTM_RSYNC_WHOLE_FILE", default=True),
                "timeout_sec": _env_int("YTM_RSYNC_TIMEOUT_SEC", default=60),
                "bwlimit_kbps": _env_int("YTM_RSYNC_BWLIMIT_KBPS", default=0),
            },
        },
        "paths": {
            "repo_root": str(REPO_ROOT),
            "src_root": str(src_root),
            "dest_root": str(dest_root),
        },
        "result": {"returncode": None, "steps": []},
    }

    _ensure_dir(_logs_dir())
    rp = _report_path(stamp)

    if not bool(args.run):
        report["result"]["returncode"] = 0
        rp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[workspaces_mirror] report: {rp}")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    final_rc = 0
    for item in commands:
        if not bool(item.get("enabled", True)):
            report["result"]["steps"].append({"name": item.get("name"), "skipped": True})
            continue
        if item.get("dest_dir"):
            try:
                Path(str(item["dest_dir"])).mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
        cmd = [str(x) for x in (item.get("cmd") or [])]
        t0 = time.monotonic()
        try:
            nice_value = _env_int("YTM_MIRROR_NICE", default=10)

            def _preexec() -> None:
                if nice_value != 0:
                    try:
                        os.nice(int(nice_value))
                    except Exception:
                        pass

            proc = subprocess.run(
                cmd,
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                errors="replace",
                check=False,
                preexec_fn=_preexec,
            )
        except FileNotFoundError:
            raise SystemExit("[POLICY] rsync not found (required).")
        step_rc = int(proc.returncode)
        dur = time.monotonic() - t0

        stdout_path: Path | None
        stderr_path: Path | None
        stdout_path, stderr_path = _step_log_paths(stamp=stamp, step_name=str(item.get("name") or "step"))
        try:
            stdout_path.write_text(proc.stdout or "", encoding="utf-8")
            stderr_path.write_text(proc.stderr or "", encoding="utf-8")
        except Exception:
            # Logging must not break the mirror.
            stdout_path = None
            stderr_path = None

        report["result"]["steps"].append(
            {
                "name": item.get("name"),
                "returncode": step_rc,
                "duration_sec": round(float(dur), 3),
                "stdout_path": str(stdout_path) if stdout_path is not None else None,
                "stderr_path": str(stderr_path) if stderr_path is not None else None,
                "stdout_tail": str(proc.stdout or "")[-20000:],
                "stderr_tail": str(proc.stderr or "")[-20000:],
            }
        )
        if step_rc != 0 and final_rc == 0:
            final_rc = step_rc

    report["result"]["returncode"] = final_rc

    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[workspaces_mirror] report: {rp}")
    if final_rc != 0:
        print("[workspaces_mirror] rsync failed (see report stderr)", file=sys.stderr)
    return int(final_rc)


if __name__ == "__main__":
    raise SystemExit(main())

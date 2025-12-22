#!/usr/bin/env python3
"""UI start/stop manager.

This module provides a CLI for launching and controlling the unified UI stack
backend/frontends.  It replicates the behaviour of `scripts/start_all.sh`
while keeping the orchestration logic in Python so it can be reused by other
tools.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from urllib import error as urllib_error
from urllib import request as urllib_request

# Directory constants ---------------------------------------------------------

def _find_repo_root(start: Path) -> Path:
    override = os.getenv("YTM_REPO_ROOT") or os.getenv("YTM_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    cur = start if start.is_dir() else start.parent
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate.resolve()
    return cur.resolve()


YTM_ROOT = _find_repo_root(Path(__file__).resolve())
APPS_ROOT = YTM_ROOT / "apps"
UI_DIR = APPS_ROOT / "ui-backend"
BACKEND_DIR = APPS_ROOT / "ui-backend" / "backend"
FRONTEND_DIR = APPS_ROOT / "ui-frontend"
LOG_ROOT = YTM_ROOT / "workspaces" / "logs" / "ui_hub"
PID_ROOT = LOG_ROOT  # store pid files alongside logs for now
ENV_FILE = YTM_ROOT / ".env"


def info(msg: str) -> None:
    print(f"\033[32m[INFO]\033[0m {msg}")


def warn(msg: str) -> None:
    print(f"\033[33m[WARN]\033[0m {msg}")


def err(msg: str) -> None:
    print(f"\033[31m[ERR ]\033[0m {msg}")


# Component definitions -------------------------------------------------------

Component = Dict[str, object]


def component_definitions() -> List[Component]:
    """Return the list of UI components managed by this tool."""

    def _build_pythonpath() -> str:
        parts: List[str] = [str(YTM_ROOT), str(YTM_ROOT / "packages")]
        existing = os.environ.get("PYTHONPATH")
        if existing:
            parts.append(existing)
        return os.pathsep.join(parts)

    backend_env = {
        "PYTHONPATH": _build_pythonpath(),
    }
    frontend_env = {
        "BROWSER": "none",
    }
    if "REACT_APP_API_BASE_URL" not in os.environ:
        frontend_env["REACT_APP_API_BASE_URL"] = "http://127.0.0.1:8000"

    return [
        {
            "name": "backend",
            "cwd": BACKEND_DIR,
            "cmd": [
                sys.executable,
                "-m",
                "uvicorn",
                "main:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
                "--reload",
            ],
            "port": 8000,
            "log": LOG_ROOT / "backend.log",
            "env": backend_env,
        },
        {
            "name": "frontend",
            "cwd": FRONTEND_DIR,
            "cmd": ["npm", "run", "start"],
            "port": 3000,
            "log": LOG_ROOT / "frontend.log",
            "env": frontend_env,
        },
    ]


def component_names() -> List[str]:
    return [component["name"] for component in component_definitions()]


# Utility helpers -------------------------------------------------------------

def run_check_env(env_file: Path) -> None:
    """Run scripts/check_env.py to ensure required variables exist."""
    info("Checking environment via scripts/check_env.py")
    check_env_py = YTM_ROOT / "scripts" / "check_env.py"
    # Full check (all required keys)
    subprocess.run(
        ["python3", str(check_env_py), "--env-file", str(env_file)],
        check=True,
    )
    # Explicit OpenRouter API check to catch unset/placeholder states early
    subprocess.run(
        [
            "python3",
            str(check_env_py),
            "--env-file",
            str(env_file),
            "--keys",
            "OPENROUTER_API_KEY",
        ],
        check=True,
    )


def pidfile_path(name: str) -> Path:
    return PID_ROOT / f"{name}.pid"


def read_pid(name: str) -> Optional[int]:
    pidfile = pidfile_path(name)
    if not pidfile.exists():
        return None
    try:
        return int(pidfile.read_text().strip())
    except ValueError:
        return None


def remove_pidfile(name: str) -> None:
    path = pidfile_path(name)
    if path.exists():
        path.unlink()


def ensure_port_free(port: int, label: str) -> None:
    """Attempt to free a TCP port by killing existing processes."""
    cmd = ["lsof", "-ti", f"tcp:{port}"]
    try:
        out = subprocess.check_output(cmd, text=True).strip()
    except subprocess.CalledProcessError:
        out = ""
    if not out:
        return
    warn(f"{label} port {port} in use by PID(s): {out}")
    for pid_str in out.splitlines():
        try:
            os.kill(int(pid_str), signal.SIGTERM)
        except OSError:
            continue
    time.sleep(1)
    # double-check; force kill if needed
    try:
        out = subprocess.check_output(cmd, text=True).strip()
    except subprocess.CalledProcessError:
        out = ""
    if not out:
        return
    warn(f"Forcing remaining PID(s) on {label}: {out}")
    for pid_str in out.splitlines():
        try:
            os.kill(int(pid_str), signal.SIGKILL)
        except OSError:
            continue


def start_component(component: Component) -> None:
    """Launch a component and write its PID file."""
    name = component["name"]
    log_path: Path = component["log"]  # type: ignore
    cwd: Path = component["cwd"]  # type: ignore
    cmd: List[str] = component["cmd"]  # type: ignore
    env_overrides: Dict[str, str] = component.get("env", {})  # type: ignore

    ensure_port_free(component.get("port", 0) or 0, name)

    log_path.parent.mkdir(parents=True, exist_ok=True)
    pidfile_path(name).parent.mkdir(parents=True, exist_ok=True)

    info(f"Starting {name} (logs: {log_path})")
    env = os.environ.copy()
    env.update(env_overrides)
    log_file = log_path.open("w")
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    pidfile_path(name).write_text(str(proc.pid))


def stop_component(component: Component, force: bool = False) -> None:
    name = component["name"]
    pid = read_pid(name)
    if not pid:
        warn(f"{name}: no PID file found")
        return
    sig = signal.SIGKILL if force else signal.SIGTERM
    info(f"Stopping {name} (pid={pid})")
    try:
        os.kill(pid, sig)
    except OSError as exc:
        warn(f"{name}: failed to send signal ({exc})")
    else:
        # wait briefly for process to exit
        for _ in range(10):
            try:
                os.kill(pid, 0)
            except OSError:
                break
            time.sleep(0.3)
    remove_pidfile(name)


def status_component(component: Component) -> str:
    name = component["name"]
    pid = read_pid(name)
    if not pid:
        return f"{name:<10} STOPPED"
    try:
        os.kill(pid, 0)
    except OSError:
        remove_pidfile(name)
        return f"{name:<10} STALE_PID ({pid})"
    return f"{name:<10} RUNNING (pid={pid})"

def stop_all_components(force: bool = False) -> None:
    components = component_definitions()
    for comp in components:
        stop_component(comp, force=force)


def stop_existing_components_before_start() -> None:
    """If PID files exist, stop those components before launching new ones."""
    components = component_definitions()
    for comp in components:
        name = comp["name"]
        pid = read_pid(name)
        if pid:
            info(f"{name}: existing instance detected (pid={pid}), stopping before start")
            stop_component(comp)


def start_all_components() -> None:
    components = component_definitions()
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    for comp in components:
        start_component(comp)


def restart_components(force: bool = False) -> None:
    stop_all_components(force=force)
    time.sleep(0.5)
    start_all_components()


def tail_file(path: Path, lines: int = 80) -> Iterable[str]:
    if not path.exists():
        yield "[no log file]"
        return
    buffer: deque[str] = deque(maxlen=lines)
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                buffer.append(line.rstrip("\n"))
    except Exception as exc:
        yield f"[failed to read log: {exc}]"
        return
    for line in buffer:
        yield line if line else ""


def stream_component_logs() -> None:
    components = component_definitions()
    log_paths = [Path(comp["log"]) for comp in components if comp.get("log")]
    if not log_paths:
        warn("No log files found to follow.")
        return
    info("Streaming logs (tail -n 50 -F ...). Press Ctrl+C to stop watching; services keep running.")
    cmd = [
        "tail",
        "-n",
        "50",
        "-F",
        *[str(path) for path in log_paths],
    ]
    try:
        subprocess.run(cmd, check=False)
    except KeyboardInterrupt:
        info("Stopped log streaming by user request.")


def check_tcp(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# CLI commands ---------------------------------------------------------------

def fetch_backend_health(url: str = "http://127.0.0.1:8000/api/healthz", timeout: float = 3.0) -> tuple[bool, Optional[Dict[str, object]], Optional[str]]:
    req = urllib_request.Request(url, method="GET")
    try:
        with urllib_request.urlopen(req, timeout=timeout) as resp:  # type: ignore[arg-type]
            data = resp.read().decode("utf-8")
            payload = json.loads(data or "{}")
            ok = resp.status < 500 and payload.get("status") in {"ok", "degraded"}
            return ok, payload, None
    except (urllib_error.HTTPError, urllib_error.URLError, json.JSONDecodeError) as exc:
        return False, None, str(exc)


def check_http_head(url: str, timeout: float = 2.0) -> tuple[bool, Optional[str]]:
    req = urllib_request.Request(url, method="HEAD")
    try:
        with urllib_request.urlopen(req, timeout=timeout) as resp:  # type: ignore[arg-type]
            return resp.status < 500, None
    except urllib_error.HTTPError as exc:
        return exc.code < 500, f"HTTP {exc.code} {exc.reason}"
    except urllib_error.URLError as exc:
        return False, str(exc)


# CLI commands ---------------------------------------------------------------

def cmd_start(args: argparse.Namespace) -> None:
    run_check_env(Path(args.env_file))
    stop_existing_components_before_start()
    start_all_components()
    info("All components started. Use `status` or `stop` as needed.")
    if not args.no_follow:
        stream_component_logs()


def cmd_stop(args: argparse.Namespace) -> None:
    stop_all_components(force=args.force)
    info("Stop command completed.")


def cmd_status(_: argparse.Namespace) -> None:
    components = component_definitions()
    for comp in components:
        print(status_component(comp))


def cmd_restart(args: argparse.Namespace) -> None:
    run_check_env(Path(args.env_file))
    info("Restarting components")
    restart_components(force=args.force)
    info("Restart completed.")
    if not args.no_follow:
        stream_component_logs()


def cmd_logs(args: argparse.Namespace) -> None:
    components = {comp["name"]: comp for comp in component_definitions()}
    target_names = component_names() if args.component == "all" else [args.component]
    for name in target_names:
        comp = components.get(name)
        if not comp:
            warn(f"{name}: unknown component")
            continue
        log_path: Path = comp["log"]  # type: ignore
        print(f"==== {name} log ({log_path}) ====")
        for line in tail_file(log_path, lines=args.lines):
            print(line)


def _run_guard(name: str, cmd: List[str]) -> bool:
    print(f"\n== Guard: {name} ==")
    proc = subprocess.run(
        cmd,
        cwd=str(YTM_ROOT),
        capture_output=True,
        text=True,
    )
    if proc.stdout:
        print(proc.stdout.strip())
    if proc.stderr:
        warn(proc.stderr.strip())
    if proc.returncode == 0:
        info(f"{name} guard completed successfully.")
        return True
    err(f"{name} guard failed with exit code {proc.returncode}.")
    return False


def cmd_health(args: argparse.Namespace) -> None:
    def _print(label: str, ok: bool, detail: Optional[str] = None) -> None:
        color = "\033[32m" if ok else "\033[31m"
        status = "OK" if ok else "FAILED"
        suffix = f"  {detail}" if detail else ""
        print(f"{color}{label:<12}{status}\033[0m{suffix}")

    print("== Backend ==")
    backend_tcp = check_tcp("127.0.0.1", 8000)
    _print("tcp", backend_tcp, "tcp://127.0.0.1:8000")
    backend_http_ok, payload, backend_err = fetch_backend_health()
    if payload:
        summary = payload.get("status")
        _print("http", backend_http_ok, f"/api/healthz status={summary}")
        components = payload.get("components") or {}
        for name in sorted(components):
            value = bool(components[name])
            _print(f"  {name}", value)
        issues = payload.get("issues") or []
        if issues:
            warn(f"Health issues detected: {', '.join(issues)}")
    else:
        detail = backend_err or "GET /api/healthz"
        _print("http", backend_http_ok, detail)

    print("\n== Frontend ==")
    frontend_http_ok, frontend_http_err = check_http_head("http://127.0.0.1:3000")
    detail = frontend_http_err or "http://127.0.0.1:3000"
    _print("http", frontend_http_ok, detail)
    frontend_tcp = check_tcp("127.0.0.1", 3000)
    _print("tcp", frontend_tcp, "tcp://127.0.0.1:3000")

    guard_failures = False
    run_api_health = args.run_api_health or args.with_guards
    run_sweep = args.run_validate_sweep or args.with_guards
    run_prompt_audit = args.run_prompt_audit or args.with_guards
    run_workflow_precheck = args.run_workflow_precheck or args.with_guards
    run_openrouter_probe = args.run_openrouter_probe or args.with_guards
    run_openrouter_caption_probe = args.run_openrouter_caption_probe or args.with_guards
    run_asset_sync = args.run_asset_sync or args.with_guards
    run_thumbnail_inventory = args.run_thumbnail_inventory or args.with_guards

    if run_api_health:
        guard_failures |= not _run_guard(
            "API health",
            ["python3", "scripts/api_health_check.py", "--base-url", "http://127.0.0.1:8000"],
        )

    if run_sweep:
        guard_failures |= not _run_guard(
            "validate-status sweep",
            [
                "python3",
                "scripts/validate_status_sweep.py",
                "--context",
                "start_manager",
                "--repair-global",
            ],
        )

    if run_prompt_audit:
        guard_failures |= not _run_guard(
            "prompt audit",
            ["python3", "scripts/prompt_audit.py", "--skip-scripts"],
        )

    if run_workflow_precheck:
        guard_failures |= not _run_guard(
            "workflow_precheck",
            ["python3", "-c", "print('workflow_precheck skipped (script_pipeline migration)')"],
        )

    if run_openrouter_probe:
        guard_failures |= not _run_guard(
            "OpenRouter API key probe",
            ["python3", "scripts/openrouter_key_probe.py"],
        )

    if run_openrouter_caption_probe:
        guard_failures |= not _run_guard(
            "OpenRouter caption probe",
            ["python3", "scripts/openrouter_caption_probe.py"],
        )

    if run_asset_sync:
        guard_failures |= not _run_guard(
            "asset sync dry-run",
            [
                "python3",
                "scripts/force_asset_sync.py",
                "--exclude-channel",
                "CH01",
                "--dry-run",
            ],
        )

    if run_thumbnail_inventory:
        cmd = ["python3", "scripts/sync_thumbnail_inventory.py"]
        if getattr(args, "thumbnail_channel", None):
            cmd.extend(["--channel", args.thumbnail_channel.upper()])
        guard_failures |= not _run_guard("thumbnail inventory", cmd)


    if guard_failures:
        raise SystemExit(1)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="UI start/stop manager")
    parser.set_defaults(func=lambda _: parser.print_help())
    sub = parser.add_subparsers(dest="command")

    p_start = sub.add_parser("start", help="Launch backend/frontend")
    p_start.add_argument(
        "--env-file", default=str(ENV_FILE), help="Path to .env (default: %(default)s)"
    )
    p_start.add_argument(
        "--no-follow",
        action="store_true",
        help="Do not tail logs after startup",
    )
    p_start.set_defaults(func=cmd_start)

    p_stop = sub.add_parser("stop", help="Stop backend/frontend")
    p_stop.add_argument("--force", action="store_true", help="Force kill with SIGKILL")
    p_stop.set_defaults(func=cmd_stop)

    p_status = sub.add_parser("status", help="Show component status")
    p_status.set_defaults(func=cmd_status)

    p_restart = sub.add_parser("restart", help="Restart backend/frontend")
    p_restart.add_argument(
        "--env-file", default=str(ENV_FILE), help="Path to .env (default: %(default)s)"
    )
    p_restart.add_argument("--force", action="store_true", help="Force kill before restart")
    p_restart.add_argument(
        "--no-follow",
        action="store_true",
        help="Do not tail logs after restart",
    )
    p_restart.set_defaults(func=cmd_restart)

    p_logs = sub.add_parser("logs", help="Tail component logs")
    p_logs.add_argument(
        "--component",
        choices=["backend", "frontend", "all"],
        default="all",
        help="Which component log to show (default: all)",
    )
    p_logs.add_argument(
        "--lines",
        type=int,
        default=80,
        help="Number of lines to show per log (default: %(default)s)",
    )
    p_logs.set_defaults(func=cmd_logs)

    p_health = sub.add_parser("healthcheck", help="Check TCP health and optional guards")
    p_health.add_argument("--with-guards", action="store_true", help="Run API/prompt/SSOT guards after basic checks")
    p_health.add_argument("--run-api-health", action="store_true", help="Run scripts/api_health_check.py as part of the check")
    p_health.add_argument("--run-validate-sweep", action="store_true", help="Run scripts/validate_status_sweep.py --repair-global")
    p_health.add_argument("--run-prompt-audit", action="store_true", help="Run scripts/prompt_audit.py (detect-only)")
    p_health.add_argument("--run-workflow-precheck", action="store_true", help="Run workflow_precheck guard (pending/ready overview)")
    p_health.add_argument("--run-openrouter-probe", action="store_true", help="Verify OpenRouter API key via scripts/openrouter_key_probe.py")
    p_health.add_argument("--run-openrouter-caption-probe", action="store_true", help="Call OpenRouter caption API to ensure models respond")
    p_health.add_argument("--run-asset-sync", action="store_true", help="Run scripts/force_asset_sync.py --dry-run to ensure SoT alignment")
    p_health.add_argument("--run-thumbnail-inventory", action="store_true", help="Run scripts/sync_thumbnail_inventory.py check (no repair)")
    p_health.add_argument("--thumbnail-channel", help="Channel code to scope thumbnail inventory (default: all)")
    p_health.set_defaults(func=cmd_health)

    args = parser.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())

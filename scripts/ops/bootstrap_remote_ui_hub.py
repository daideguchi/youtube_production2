#!/usr/bin/env python3
"""
bootstrap_remote_ui_hub.py — Mac から Acer(UI Hub) を “触らずに” 常駐化する

Goal:
  - ユーザーが Acer にログインして手作業しなくても、Mac から 1 回の実行で
    UI Hub(systemd) + Tailscale Serve(/ui,/api,...) を整える。

SSOT / Runbook:
  - ssot/agent_runbooks/RUNBOOK_UI_HUB_DAEMON.md

Policy:
  - safe-by-default: dry-run が既定（--run 指定時のみ実行）
  - secrets を表示しない（.env の中身は絶対に print しない）
  - 破壊的操作（service 置換 / tailscale serve 再配線）は “対象だけ” を更新し、
    tailscale serve reset はしない
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from _bootstrap import bootstrap

REPO_ROOT = bootstrap(load_env=False)

DEFAULT_REMOTE_REPO_ROOT_CANDIDATES = [
    # Historical / common layout on Acer
    "/srv/workspace/doraemon/repos/youtube_production2",
    "/srv/workspace/doraemon/repos/factory_commentary",
]


def _shell_join(cmd: List[str]) -> str:
    return " ".join(shlex.quote(c) for c in cmd)


def _print_step(title: str) -> None:
    print(f"\n== {title} ==")


def _run_local(cmd: List[str], *, run: bool, input_text: Optional[str] = None) -> subprocess.CompletedProcess[str]:
    print(f"▶ {_shell_join(cmd)}")
    if not run:
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return subprocess.run(
        cmd,
        input=input_text,
        text=True,
        cwd=str(REPO_ROOT),
        capture_output=False,
        check=False,
    )


@dataclass(frozen=True)
class SshConfig:
    host: str
    timeout_sec: int
    sudo_mode: str  # noninteractive | interactive

    def ssh_base(self) -> List[str]:
        base = [
            "ssh",
            "-o",
            f"ConnectTimeout={self.timeout_sec}",
            # Prefer key auth only (faster + avoids hangs on password/kbd prompts).
            "-o",
            "GSSAPIAuthentication=no",
            "-o",
            "PreferredAuthentications=publickey",
            "-o",
            "PubkeyAuthentication=yes",
            "-o",
            "PasswordAuthentication=no",
            "-o",
            "KbdInteractiveAuthentication=no",
        ]
        if self.sudo_mode == "noninteractive":
            base += ["-o", "BatchMode=yes"]
        else:
            # Allow prompts (sudo password, etc.)
            base += ["-tt"]
        return base

    def scp_base(self) -> List[str]:
        base = [
            "scp",
            "-o",
            f"ConnectTimeout={self.timeout_sec}",
            "-o",
            "GSSAPIAuthentication=no",
            "-o",
            "PreferredAuthentications=publickey",
            "-o",
            "PubkeyAuthentication=yes",
            "-o",
            "PasswordAuthentication=no",
            "-o",
            "KbdInteractiveAuthentication=no",
        ]
        if self.sudo_mode == "noninteractive":
            base += ["-o", "BatchMode=yes"]
        return base

    def sudo_prefix(self) -> str:
        return "sudo -n" if self.sudo_mode == "noninteractive" else "sudo"


def _ssh_cmd(cfg: SshConfig, bash_script: str) -> List[str]:
    # Use bash -lc for predictable behaviour (PATH, set -euo, etc).
    script = "set -euo pipefail; " + bash_script
    # IMPORTANT: pass the remote command as a *single* ssh argument.
    # ssh concatenation can otherwise lose quoting/word boundaries for `bash -lc`.
    remote = "bash -lc " + shlex.quote(script)
    return [*cfg.ssh_base(), cfg.host, remote]


def _remote_file_exists(cfg: SshConfig, *, path: str) -> bool:
    proc = subprocess.run(
        _ssh_cmd(cfg, f"test -f {shlex.quote(path)}"),
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def _remote_dir_exists(cfg: SshConfig, *, path: str) -> bool:
    proc = subprocess.run(
        _ssh_cmd(cfg, f"test -d {shlex.quote(path)}"),
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def _remote_repo_root_is_valid(cfg: SshConfig, *, repo_root: str) -> bool:
    repo_root = str(repo_root).rstrip("/")
    required_files = [
        f"{repo_root}/start.sh",
        f"{repo_root}/apps/ui-backend/tools/start_manager.py",
        f"{repo_root}/apps/ui-frontend/package.json",
    ]
    checks = " && ".join([f"test -f {shlex.quote(p)}" for p in required_files])
    proc = subprocess.run(
        _ssh_cmd(cfg, checks),
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def _detect_remote_repo_root(cfg: SshConfig, *, candidates: List[str]) -> str | None:
    found: List[str] = []
    for c in candidates:
        c = str(c or "").strip().rstrip("/")
        if not c:
            continue
        if _remote_repo_root_is_valid(cfg, repo_root=c):
            found.append(c)
    if len(found) == 1:
        return found[0]
    return None


def _render_systemd_unit(*, repo_root: str, env_file: str, workspace_root: str) -> str:
    # NOTE: start_manager.py は env-file を override しない(setdefault)ので、
    # systemd の Environment で path 系を固定できる。
    #
    # Why:
    # - remote .env(.env.ui_hub) は secrets を含むため共有したいが、Mac 固有パスが混ざると危険。
    # - path 系は host 毎に異なるので systemd の Environment で上書きし、env-file の値に依存しない。
    shared_root = str(Path(workspace_root).parent)
    unit = f"""[Unit]
Description=Factory UI Hub (backend:8000 + frontend:3000)
After=network.target

[Service]
Type=simple
Environment=YTM_SHARED_STORAGE_ROOT={shared_root}
Environment=YTM_VAULT_WORKSPACES_ROOT={workspace_root}
Environment=YTM_ASSET_VAULT_ROOT={shared_root}/asset_vault
Environment=YTM_PLANNING_ROOT={workspace_root}/planning
Environment=YTM_WORKSPACE_ROOT={workspace_root}
WorkingDirectory={repo_root}
ExecStart={repo_root}/.venv/bin/python3 apps/ui-backend/tools/start_manager.py start --env-file {repo_root}/{env_file} --profile prod --frontend-script serve:acer --supervise --no-follow
ExecStop={repo_root}/.venv/bin/python3 apps/ui-backend/tools/start_manager.py stop
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""
    return unit


def _plan_tailscale_serve_commands(*, sudo_prefix: str) -> List[str]:
    # Tailscale Serve CLI supports per-path wiring via --set-path.
    # Keep /files etc intact by only setting the required paths.
    # NOTE: we do NOT call `tailscale serve reset`.
    base = f"{sudo_prefix} tailscale serve --yes --https 443"
    return [
        f"{base} --set-path /ui 3000",
        f"{base} --set-path /api 8000",
        f"{base} --set-path /thumbnails/assets http://127.0.0.1:8000",
        f"{base} --set-path /thumbnails/library http://127.0.0.1:8000",
        # Optional (Remotion preview):
        f"{base} --set-path /remotion 3000",
    ]

def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Bootstrap UI Hub daemon on a remote host (Mac -> Acer) (dry-run by default)")
    p.add_argument("--host", required=True, help="ssh target (e.g. 'acer' or user@host)")
    p.add_argument(
        "--remote-repo-root",
        required=True,
        help="absolute path to this repo on the remote host (or 'auto' to detect common locations)",
    )
    p.add_argument("--workspace-root", required=True, help="remote path to shared ytm_workspaces (SoT) directory")
    p.add_argument(
        "--remote-repo-root-candidate",
        action="append",
        default=[],
        help="candidate remote repo roots for --remote-repo-root=auto (repeatable)",
    )

    p.add_argument("--service-name", default="factory_ui_hub.service", help="systemd unit name (default: %(default)s)")
    p.add_argument("--env-file-name", default=".env.ui_hub", help="remote env filename under repo root (default: %(default)s)")

    p.add_argument(
        "--sudo-mode",
        choices=["noninteractive", "interactive"],
        default="noninteractive",
        help="sudo/ssh mode (default: %(default)s). Use interactive only if you must type a sudo password.",
    )
    p.add_argument("--timeout-sec", type=int, default=8, help="ssh/scp connect timeout seconds (default: %(default)s)")

    p.add_argument(
        "--sync-env",
        action="store_true",
        help="copy local .env -> remote <repo>/.env.ui_hub (never prints contents). Existing file is kept unless --overwrite-env.",
    )
    p.add_argument("--overwrite-env", action="store_true", help="overwrite remote env file when --sync-env is set")

    p.add_argument(
        "--ensure-deps",
        action="store_true",
        help="on remote: ensure .venv + pip deps + frontend build exist (idempotent; can take time)",
    )
    p.add_argument(
        "--recover-tailscale",
        action="store_true",
        help="on remote: if workspace_root is missing, try to force-restart tailscaled and re-check (safe-by-default; requires sudo)",
    )
    p.add_argument(
        "--configure-tailscale-serve",
        action="store_true",
        help="on remote: set tailscale serve paths (/ui,/api,/thumbnails/*,/remotion). Does not reset existing config.",
    )
    p.add_argument(
        "--configure-acer-watchdog",
        action="store_true",
        help="on remote: install a minimal systemd timer watchdog (tailscaled/ssh/samba) and publish /files/_reports/acer_watchdog.json",
    )
    p.add_argument("--run", action="store_true", help="execute (default: dry-run)")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    run = bool(args.run)
    cfg = SshConfig(host=str(args.host), timeout_sec=int(args.timeout_sec), sudo_mode=str(args.sudo_mode))

    remote_repo_root_raw = str(args.remote_repo_root).strip()
    remote_workspace_root = str(args.workspace_root).rstrip("/")
    service_name = str(args.service_name).strip()
    env_file_name = str(args.env_file_name).strip().lstrip("/")

    repo_root_candidates = [*DEFAULT_REMOTE_REPO_ROOT_CANDIDATES, *[str(x) for x in (args.remote_repo_root_candidate or [])]]
    if remote_repo_root_raw.lower() == "auto":
        detected = _detect_remote_repo_root(cfg, candidates=repo_root_candidates)
        if detected is None:
            print(
                "\n".join(
                    [
                        "[ERROR] could not auto-detect remote repo root.",
                        "Provide one of:",
                        "- --remote-repo-root <absolute path>",
                        "- or add candidates with: --remote-repo-root-candidate <path> (repeatable)",
                        f"- defaults tried: {', '.join(repo_root_candidates)}",
                    ]
                ),
                file=sys.stderr,
            )
            return 2
        remote_repo_root = detected
        print(f"[auto] remote repo root: {remote_repo_root}")
    else:
        remote_repo_root = remote_repo_root_raw.rstrip("/")

    local_env_path = (REPO_ROOT / ".env").resolve()
    remote_env_path = f"{remote_repo_root}/{env_file_name}"
    sudo = cfg.sudo_prefix()

    _print_step("Connectivity")
    _run_local(_ssh_cmd(cfg, "hostname; whoami; uptime || true"), run=run)

    _print_step("Preflight (remote paths)")
    if not _remote_dir_exists(cfg, path=remote_repo_root):
        print(f"[ERROR] remote repo root not found: {remote_repo_root}", file=sys.stderr)
        return 2
    if not _remote_dir_exists(cfg, path=remote_workspace_root):
        if bool(args.recover_tailscale):
            _print_step("Recover tailscaled (remote)")
            if cfg.sudo_mode == "noninteractive":
                # Fail fast if sudo would prompt.
                _run_local(_ssh_cmd(cfg, f"{sudo} true"), run=run)
            recover_script = "\n".join(
                [
                    # Be conservative: kill stuck cleanup, then start.
                    f"{sudo} systemctl kill --kill-who=all --signal=KILL tailscaled.service || true",
                    f"{sudo} systemctl reset-failed tailscaled.service || true",
                    f"{sudo} systemctl start tailscaled.service || true",
                    f"{sudo} systemctl is-active tailscaled.service || true",
                ]
            )
            _run_local(_ssh_cmd(cfg, recover_script), run=run)

        if not _remote_dir_exists(cfg, path=remote_workspace_root):
            print(f"[ERROR] remote workspace root not found: {remote_workspace_root}", file=sys.stderr)
            return 2

    _print_step("Env file (remote)")
    if bool(args.sync_env):
        if not local_env_path.exists():
            print(f"[ERROR] local .env not found: {local_env_path}", file=sys.stderr)
            return 2
        if _remote_file_exists(cfg, path=remote_env_path) and not bool(args.overwrite_env):
            print(f"[skip] remote env exists: {remote_env_path} (use --overwrite-env to replace)")
        else:
            cmd = [*cfg.scp_base(), str(local_env_path), f"{cfg.host}:{remote_env_path}"]
            _run_local(cmd, run=run)
            # Tighten perms (best-effort).
            _run_local(_ssh_cmd(cfg, f"chmod 600 {shlex.quote(remote_env_path)} || true"), run=run)
    else:
        if not _remote_file_exists(cfg, path=remote_env_path):
            print(
                "\n".join(
                    [
                        f"[ERROR] remote env file missing: {remote_env_path}",
                        "Fix (choose one):",
                        f"- rerun with: --sync-env  (copies local {local_env_path.name} to remote {env_file_name})",
                        f"- or create it on remote: {remote_env_path}",
                    ]
                ),
                file=sys.stderr,
            )
            return 2

    _print_step("Optional: ensure remote deps (venv/pip/npm/build)")
    if bool(args.ensure_deps):
        dep_script = "\n".join(
            [
                f"cd {shlex.quote(remote_repo_root)}",
                "if [ ! -x .venv/bin/python3 ]; then python3 -m venv .venv; fi",
                ".venv/bin/python3 -m pip install -U pip wheel setuptools",
                # Install monorepo deps (pyproject) + UI backend deps (explicit).
                ".venv/bin/python3 -m pip install -e .",
                ".venv/bin/python3 -m pip install -r apps/ui-backend/backend/requirements.txt",
                f"cd {shlex.quote(remote_repo_root)}/apps/ui-frontend",
                "if [ ! -d node_modules ]; then npm ci; fi",
                "if [ ! -f build/index.html ]; then npm run build:acer:gz; fi",
            ]
        )
        _run_local(_ssh_cmd(cfg, dep_script), run=run)

    _print_step("Install/enable systemd service (UI Hub)")
    if cfg.sudo_mode == "noninteractive":
        # Fail fast if sudo would prompt.
        _run_local(_ssh_cmd(cfg, f"{sudo} true"), run=run)

    unit_text = _render_systemd_unit(repo_root=remote_repo_root, env_file=env_file_name, workspace_root=remote_workspace_root)
    unit_path = f"/etc/systemd/system/{service_name}"

    # Write unit via sudo tee (no secrets involved).
    tee_remote = "bash -lc " + shlex.quote(f"set -euo pipefail; {sudo} tee {shlex.quote(unit_path)} >/dev/null")
    tee_cmd = [*cfg.ssh_base(), cfg.host, tee_remote]
    _run_local(tee_cmd, run=run, input_text=unit_text)
    _run_local(_ssh_cmd(cfg, f"{sudo} systemctl daemon-reload"), run=run)
    _run_local(_ssh_cmd(cfg, f"{sudo} systemctl enable --now {shlex.quote(service_name)}"), run=run)
    # Ensure updated Environment lines take effect even if the service was already running.
    _run_local(_ssh_cmd(cfg, f"{sudo} systemctl restart {shlex.quote(service_name)}"), run=run)
    _run_local(_ssh_cmd(cfg, f"{sudo} systemctl --no-pager --full status {shlex.quote(service_name)} || true"), run=run)

    _print_step("Optional: configure tailscale serve paths (/ui,/api,...)")
    if bool(args.configure_tailscale_serve):
        # Show current status first (read-only).
        _run_local(_ssh_cmd(cfg, f"{sudo} tailscale serve status || true"), run=run)
        for c in _plan_tailscale_serve_commands(sudo_prefix=sudo):
            _run_local(_ssh_cmd(cfg, c), run=run)
        _run_local(_ssh_cmd(cfg, f"{sudo} tailscale serve status || true"), run=run)

    _print_step("Optional: install Acer watchdog (systemd timer)")
    if bool(args.configure_acer_watchdog):
        asset_dir = REPO_ROOT / "ssot" / "agent_runbooks" / "assets"
        script_text = _read_text(asset_dir / "acer_watchdog.sh")
        service_text = _read_text(asset_dir / "acer_watchdog.service")
        timer_text = _read_text(asset_dir / "acer_watchdog.timer")

        script_path = "/usr/local/bin/doraemon-acer-watchdog.sh"
        service_path = "/etc/systemd/system/doraemon-acer-watchdog.service"
        timer_path = "/etc/systemd/system/doraemon-acer-watchdog.timer"

        # Install script.
        tee_script_remote = "bash -lc " + shlex.quote(f"set -euo pipefail; {sudo} tee {shlex.quote(script_path)} >/dev/null")
        tee_script_cmd = [*cfg.ssh_base(), cfg.host, tee_script_remote]
        _run_local(tee_script_cmd, run=run, input_text=script_text)
        _run_local(_ssh_cmd(cfg, f"{sudo} chmod 755 {shlex.quote(script_path)} || true"), run=run)

        # Install units.
        tee_service_remote = "bash -lc " + shlex.quote(f"set -euo pipefail; {sudo} tee {shlex.quote(service_path)} >/dev/null")
        tee_service_cmd = [*cfg.ssh_base(), cfg.host, tee_service_remote]
        _run_local(tee_service_cmd, run=run, input_text=service_text)

        tee_timer_remote = "bash -lc " + shlex.quote(f"set -euo pipefail; {sudo} tee {shlex.quote(timer_path)} >/dev/null")
        tee_timer_cmd = [*cfg.ssh_base(), cfg.host, tee_timer_remote]
        _run_local(tee_timer_cmd, run=run, input_text=timer_text)

        _run_local(_ssh_cmd(cfg, f"{sudo} systemctl daemon-reload"), run=run)
        _run_local(_ssh_cmd(cfg, f"{sudo} systemctl enable --now doraemon-acer-watchdog.timer"), run=run)
        _run_local(_ssh_cmd(cfg, f"{sudo} systemctl --no-pager --full status doraemon-acer-watchdog.timer || true"), run=run)

    print("\n[done] Next checks (browser):")
    print("- https://<acer>.ts.net/ui/")
    print("- https://<acer>.ts.net/api/healthz")
    print("- https://<acer>.ts.net/ui/hq?go=files")
    print("- https://<acer>.ts.net/files/_reports/acer_watchdog.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

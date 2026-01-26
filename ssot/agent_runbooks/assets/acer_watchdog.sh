#!/usr/bin/env bash
set -euo pipefail

# Acer watchdog (minimal): keep key daemons responsive and publish a small heartbeat
# under /files/_reports/acer_watchdog.json (served by Tailscale Serve on Acer).
#
# Goals:
# - Fix the common "ping/22/445 OK but SSH banner times out" by restarting ssh.
# - Recover tailnet reachability by restarting tailscaled when needed.
# - Keep SMB usable enough for mounts by restarting smbd/nmbd on failures.
# - Always finish quickly (no hangs) and write a small JSON report.

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

timeout_bin="$(command -v timeout || true)"
run_timeout() {
  local sec="$1"
  shift
  if [ -n "${timeout_bin:-}" ]; then
    "$timeout_bin" "$sec" "$@"
  else
    "$@"
  fi
}

bool_json() {
  local v="$1"
  if [ "$v" -eq 1 ]; then
    echo true
  else
    echo false
  fi
}

report_dir="${ACER_WATCHDOG_REPORT_DIR:-/srv/workspace/doraemon/workspace/_reports}"
report_path="${report_dir%/}/acer_watchdog.json"

actions=()
tailscale_ok=1
ssh_banner_ok=1
smb_ok=1

# --- tailscaled / tailnet ---
if ! systemctl is-active --quiet tailscaled.service 2>/dev/null; then
  actions+=("restart:tailscaled(inactive)")
  systemctl reset-failed tailscaled.service >/dev/null 2>&1 || true
  systemctl start tailscaled.service >/dev/null 2>&1 || true
fi
if ! run_timeout 6 tailscale status >/dev/null 2>&1; then
  tailscale_ok=0
  actions+=("restart:tailscaled(status_fail)")
  systemctl restart tailscaled.service >/dev/null 2>&1 || true
  if run_timeout 8 tailscale status >/dev/null 2>&1; then
    tailscale_ok=1
  fi
fi

# --- ssh banner ---
if ! run_timeout 6 bash -lc "exec 3<>/dev/tcp/127.0.0.1/22; head -c 32 <&3" 2>/dev/null | grep -q "SSH-"; then
  ssh_banner_ok=0
  actions+=("restart:ssh(banner_timeout)")
  systemctl restart ssh.service >/dev/null 2>&1 || systemctl restart sshd.service >/dev/null 2>&1 || true
  if run_timeout 6 bash -lc "exec 3<>/dev/tcp/127.0.0.1/22; head -c 32 <&3" 2>/dev/null | grep -q "SSH-"; then
    ssh_banner_ok=1
  fi
fi

# --- samba (best-effort) ---
smbd_load="$(systemctl show smbd.service -p LoadState --value 2>/dev/null || true)"
if [ -n "${smbd_load:-}" ] && [ "${smbd_load:-}" != "not-found" ]; then
  if ! systemctl is-active --quiet smbd.service 2>/dev/null; then
    smb_ok=0
    actions+=("restart:smbd(inactive)")
    systemctl restart smbd.service nmbd.service >/dev/null 2>&1 || true
  fi
  if command -v smbstatus >/dev/null 2>&1; then
    if ! run_timeout 6 smbstatus >/dev/null 2>&1; then
      smb_ok=0
      actions+=("restart:samba(smbstatus_fail)")
      systemctl restart smbd.service nmbd.service >/dev/null 2>&1 || true
    fi
  fi
fi

ok=1
if [ "$tailscale_ok" -eq 0 ] || [ "$ssh_banner_ok" -eq 0 ] || [ "$smb_ok" -eq 0 ]; then
  ok=0
fi

actions_json="[]"
if [ "${#actions[@]}" -gt 0 ]; then
  actions_json="["
  for a in "${actions[@]}"; do
    actions_json="${actions_json}\"${a}\","
  done
  actions_json="${actions_json%,}]"
fi

umask 022
mkdir -p "$report_dir" >/dev/null 2>&1 || true
tmp="${report_path}.tmp"
cat >"$tmp" <<EOF
{"ts":"$(ts)","host":"$(hostname -s 2>/dev/null || hostname)","ok":$(bool_json "$ok"),"tailscale_ok":$(bool_json "$tailscale_ok"),"ssh_banner_ok":$(bool_json "$ssh_banner_ok"),"smb_ok":$(bool_json "$smb_ok"),"actions":$actions_json}
EOF
mv -f "$tmp" "$report_path" >/dev/null 2>&1 || true


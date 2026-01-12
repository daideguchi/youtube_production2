#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from _bootstrap import bootstrap

PROJECT_ROOT = Path(bootstrap())

from factory_common.paths import logs_root, repo_root

# ---------------------------------------------------------------------------
# Ops run context (best-effort)
#
# Purpose:
# - Keep Slack notifications actionable: include where to look next (logs/pointers).
# - Avoid "exit=2" spam when the underlying tool uses exit=2 to signal WARN (non-fatal).
# ---------------------------------------------------------------------------
_OPS_RUN_ID: str | None = None
_OPS_TOP_CMD: str | None = None
_OPS_TOP_OP: str | None = None
_OPS_CAPTURE_RUN_LOGS: bool = False
_OPS_RUN_SEQ: int = 0
_OPS_RUN_LOG_DIR: Path | None = None
_OPS_LAST_RUN_LOG: Path | None = None
_OPS_LAST_FAILED_RUN: dict | None = None
_OPS_LAST_WARN_RUN: dict | None = None
_OPS_WARNINGS: dict | None = None
_OPS_EXIT_CODE_RAW: int | None = None


def _root() -> Path:
    # Prefer canonical helper (avoid Path(__file__).parents footguns).
    return repo_root()


def _env_with_llm_exec_slot(slot: int) -> Dict[str, str]:
    env = dict(os.environ)
    env["LLM_EXEC_SLOT"] = str(int(slot))
    return env


def _ops_runs_dir() -> Path:
    return _ops_log_dir() / "runs"


def _set_ops_run_context(*, run_id: str, cmd: str | None, op: str | None, capture_logs: bool) -> None:
    global _OPS_RUN_ID, _OPS_TOP_CMD, _OPS_TOP_OP, _OPS_CAPTURE_RUN_LOGS, _OPS_RUN_SEQ, _OPS_RUN_LOG_DIR
    global _OPS_LAST_RUN_LOG, _OPS_LAST_FAILED_RUN, _OPS_LAST_WARN_RUN, _OPS_WARNINGS, _OPS_EXIT_CODE_RAW
    _OPS_RUN_ID = str(run_id or "").strip() or None
    _OPS_TOP_CMD = str(cmd or "").strip().lower() or None
    _OPS_TOP_OP = str(op or "").strip().lower() or None
    _OPS_CAPTURE_RUN_LOGS = bool(capture_logs)
    _OPS_RUN_SEQ = 0
    _OPS_RUN_LOG_DIR = _ops_runs_dir() / _OPS_RUN_ID if _OPS_RUN_ID else None
    _OPS_LAST_RUN_LOG = None
    _OPS_LAST_FAILED_RUN = None
    _OPS_LAST_WARN_RUN = None
    _OPS_WARNINGS = None
    _OPS_EXIT_CODE_RAW = None


def _safe_log_stem(raw: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(raw or "").strip())
    s = s.strip("._-") or "run"
    return s[:80]


def _log_label_for_argv(argv: List[str]) -> str:
    toks = [str(x) for x in (argv or []) if str(x).strip()]
    if not toks:
        return "run"
    # Prefer "python3 <script> <subcmd>" → "<script>__<subcmd>"
    if len(toks) >= 2 and toks[0].endswith("python3"):
        script = Path(toks[1]).name
        stem = script[:-3] if script.endswith(".py") else script
        sub = toks[2] if len(toks) >= 3 and not toks[2].startswith("-") else ""
        return _safe_log_stem("__".join([p for p in [stem, sub] if p]))
    # Otherwise: "<cmd>__<arg1>"
    head = Path(toks[0]).name
    sub = toks[1] if len(toks) >= 2 and not toks[1].startswith("-") else ""
    return _safe_log_stem("__".join([p for p in [head, sub] if p]))


def _run(argv: List[str], *, env: Optional[Dict[str, str]] = None) -> int:
    """
    Run an inner command.
    - Default: inherit stdio (operator-friendly).
    - When capture is enabled for this ops run, tee stdout/stderr to a per-run log file
      under workspaces/logs/ops/ops_cli/runs/<run_id>/.
    """
    global _OPS_RUN_SEQ, _OPS_LAST_RUN_LOG, _OPS_LAST_FAILED_RUN, _OPS_LAST_WARN_RUN

    if not (_OPS_CAPTURE_RUN_LOGS and _OPS_RUN_LOG_DIR):
        proc = subprocess.run(argv, cwd=str(_root()), env=env)
        return int(proc.returncode)

    _OPS_RUN_SEQ += 1
    label = _log_label_for_argv(argv)
    log_path = _OPS_RUN_LOG_DIR / f"{_OPS_RUN_SEQ:02d}__{label}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Merge stderr into stdout for a single readable log (keep it simple).
    with log_path.open("w", encoding="utf-8", errors="replace") as out:
        out.write("# argv: " + " ".join([str(x) for x in argv]) + "\n")
        out.flush()
        proc = subprocess.Popen(
            argv,
            cwd=str(_root()),
            env=env,
            stdin=None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            try:
                sys.stdout.write(line)
                sys.stdout.flush()
            except Exception:
                pass
            try:
                out.write(line)
                out.flush()
            except Exception:
                pass
        rc = int(proc.wait())

    _OPS_LAST_RUN_LOG = log_path
    if rc != 0:
        _OPS_LAST_FAILED_RUN = {"argv": [str(x) for x in argv], "exit_code": rc, "log_path": str(log_path)}
    return rc


def _ops_latest_pointer_path_for_event(event_finish: dict) -> Path:
    """
    Convenience: where `./ops latest` would read from.
    (We still write latest pointers separately.)
    """
    latest_dir = _ops_latest_dir()
    ep = event_finish.get("episode") if isinstance(event_finish.get("episode"), dict) else {}
    episode_id = str(ep.get("episode_id") or "").strip() if isinstance(ep, dict) else ""
    if episode_id:
        return latest_dir / f"{episode_id}.json"
    cmd = str(event_finish.get("cmd") or "").strip().lower()
    if cmd:
        return latest_dir / f"cmd__{cmd}.json"
    return latest_dir / "latest.json"


def _set_ops_warnings(*, warnings: List[str], manifest_path: Path | None = None, note: str | None = None) -> None:
    global _OPS_WARNINGS
    items = [str(w).strip() for w in (warnings or []) if str(w).strip()]
    payload: dict = {"count": len(items), "items": items[:8]}
    if manifest_path is not None:
        payload["manifest_path"] = str(manifest_path)
    if note:
        payload["note"] = str(note)
    _OPS_WARNINGS = payload



def _run_think(inner_cmd: List[str]) -> int:
    """
    THINK MODE runner (subscription/manual completion):
    - uses scripts/think.sh to enqueue pending tasks + write bundles
    - default interception: ALL LLM tasks except image_generation/visual_image_gen
    """
    cmd = ["bash", str(_root() / "scripts" / "think.sh"), "--", *inner_cmd]
    return _run(cmd)


def _strip_leading_double_dash(argv: List[str]) -> List[str]:
    """
    Support common passthrough idiom:
      ./ops <cmd> ... -- <args for inner tool>
    Strip a single leading '--' from forwarded argv.
    """
    if argv and argv[0] == "--":
        return argv[1:]
    return argv


def _find_flag_value(argv: List[str], flag: str) -> Optional[str]:
    """
    Minimal argv scanner for common patterns:
      --flag VALUE
      --flag=VALUE
    """
    for i, token in enumerate(argv):
        if token == flag and i + 1 < len(argv):
            return str(argv[i + 1])
        if token.startswith(flag + "="):
            return str(token.split("=", 1)[1])
    return None


def _collect_flag_values(argv: List[str], flag: str) -> List[str]:
    """
    Collect values for:
      --flag V1 V2 ...  (until next option or end)
    and:
      --flag=V
    """
    out: List[str] = []
    for i, token in enumerate(argv):
        if token.startswith(flag + "="):
            out.append(str(token.split("=", 1)[1]))
            continue
        if token != flag:
            continue
        j = i + 1
        while j < len(argv):
            nxt = argv[j]
            if str(nxt).startswith("-"):
                break
            out.append(str(nxt))
            j += 1
    return out


def _normalize_channel(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    s = str(value).strip().upper()
    return s if re.fullmatch(r"CH\d{2}", s) else None


def _normalize_video(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s.isdigit():
        return s.zfill(3)
    return s


def _drop_flag_with_value(argv: List[str], flag: str) -> List[str]:
    out: List[str] = []
    skip_next = False
    for token in argv:
        if skip_next:
            skip_next = False
            continue
        if token == flag:
            skip_next = True
            continue
        if token.startswith(flag + "="):
            continue
        out.append(token)
    return out


def _drop_flag(argv: List[str], flag: str) -> List[str]:
    return [t for t in argv if t != flag and not t.startswith(flag + "=")]


def _extract_llm_flag(argv: List[str]) -> tuple[str, List[str]]:
    llm: Optional[str] = None
    out: List[str] = []
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "--llm" and i + 1 < len(argv):
            llm = str(argv[i + 1])
            i += 2
            continue
        if token.startswith("--llm="):
            llm = str(token.split("=", 1)[1])
            i += 1
            continue
        out.append(token)
        i += 1

    mode = _normalize_llm_mode(llm) if llm is not None else _ops_default_llm_mode()
    if mode not in {"api", "think", "codex"}:
        raise SystemExit(f"invalid --llm: {llm} (expected: api|think|codex)")
    return str(mode), out


def _normalize_llm_mode(raw: str | None) -> str | None:
    s = str(raw or "").strip().lower()
    if not s:
        return None
    # Accept a few human-ish aliases (keep conservative).
    if s in {"think_mode", "think-mode", "thinkmode", "t"}:
        return "think"
    if s in {"codex_exec", "codex-exec", "codexexec", "c"}:
        return "codex"
    if s in {"api_mode", "api-mode", "apimode", "a"}:
        return "api"
    if s in {"api", "think", "codex"}:
        return s
    return None


def _ops_force_llm_mode() -> str | None:
    # Force mode for this `./ops` invocation (set by `./ops think|api|codex ...`).
    return _normalize_llm_mode(os.getenv("YTM_OPS_FORCE_LLM"))

def _ops_force_exec_slot() -> int | None:
    """
    Optional exec-slot override for *this ops invocation* (advanced).

    Motivation:
    - Default behavior maps `--llm api|think|codex` to exec-slot (0/3/1).
    - Operators sometimes need to pin an exec-slot for a one-off run without editing configs
      or exporting env vars globally.

    Note:
    - This is an ops wrapper feature; it only affects processes spawned by `./ops`.
    - Script pipeline remains API-only and ignores this override (policy).
    """
    raw = (os.getenv("YTM_OPS_FORCE_EXEC_SLOT") or "").strip()
    if not raw:
        return None
    try:
        return max(0, int(raw))
    except Exception:
        return None


def _apply_ops_runtime_overrides(args: argparse.Namespace) -> None:
    """
    Apply *per-run* override knobs for ops_cli itself.

    This is intentionally env-backed so wrapper commands (`./ops api|think|codex ...`) re-exec
    with the same overrides without needing to forward flags.
    """
    llm_slot = getattr(args, "llm_slot", None)
    if llm_slot is not None:
        try:
            os.environ["LLM_MODEL_SLOT"] = str(max(0, int(llm_slot)))
        except Exception:
            raise SystemExit(f"invalid --llm-slot: {llm_slot!r} (expected int)")

    exec_slot = getattr(args, "exec_slot", None)
    if exec_slot is not None:
        try:
            os.environ["YTM_OPS_FORCE_EXEC_SLOT"] = str(max(0, int(exec_slot)))
        except Exception:
            raise SystemExit(f"invalid --exec-slot: {exec_slot!r} (expected int)")

    if bool(getattr(args, "emergency_override", False)):
        os.environ["YTM_EMERGENCY_OVERRIDE"] = "1"


def _ops_default_llm_mode() -> str:
    # Default mode when a command doesn't pass `--llm`.
    forced = _ops_force_llm_mode()
    if forced:
        return forced
    raw = os.getenv("YTM_OPS_DEFAULT_LLM") or os.getenv("YTM_DEFAULT_LLM") or ""
    return _normalize_llm_mode(raw) or "api"


def _apply_forced_llm(llm: str | None) -> str:
    forced = _ops_force_llm_mode()
    if forced:
        return forced
    return _normalize_llm_mode(llm) or _ops_default_llm_mode()


def _extract_doctor_flag(argv: List[str]) -> tuple[bool, List[str]]:
    """
    Default: run doctor before resume.
    - --no-doctor / --skip-doctor disables it.
    - --doctor forces it on.
    """
    doctor = True
    out: List[str] = []
    for token in argv:
        if token in {"--no-doctor", "--skip-doctor"}:
            doctor = False
            continue
        if token == "--doctor":
            doctor = True
            continue
        out.append(token)
    return doctor, out


def _resolve_final_srt_path(*, channel: str, video: str) -> Path:
    from factory_common.timeline_manifest import EpisodeId, resolve_final_audio_srt

    _wav, srt = resolve_final_audio_srt(EpisodeId(channel=channel, video=video))
    return srt


def _extract_episode_from_argv(argv: List[str]) -> dict:
    """
    Best-effort extraction for filtering ops history.
    This is NOT a new SoT; it's for a convenience ledger.
    """
    top_cmd = str(argv[0]).strip() if argv else ""
    if top_cmd in {"history", "list", "doctor", "inventory", "ssot"}:
        return {"channel": None, "video": None, "videos": None, "run_dir": None, "episode_id": None}

    channel = _normalize_channel(_find_flag_value(argv, "--channel"))
    video = _normalize_video(_find_flag_value(argv, "--video"))

    videos: List[str] = []
    for v in _collect_flag_values(argv, "--videos"):
        if "," in v:
            videos.extend([_normalize_video(x) or "" for x in v.split(",")])
        else:
            videos.append(_normalize_video(v) or "")
    videos = [v for v in videos if v]

    run_dir = _find_flag_value(argv, "--run")
    if not channel and run_dir:
        m = re.search(r"(CH\d{2})", str(run_dir).upper())
        if m:
            channel = m.group(1)
    if not video and run_dir:
        m = re.search(r"(?:^|[^0-9])(\d{3})(?:[^0-9]|$)", str(run_dir))
        if m:
            video = m.group(1)

    episode_id = f"{channel}-{video}" if channel and video else None
    return {
        "channel": channel,
        "video": video,
        "videos": videos or None,
        "run_dir": run_dir,
        "episode_id": episode_id,
    }


def _op_from_args(args: argparse.Namespace) -> Optional[str]:
    for key in ("target", "action", "mode", "kind"):
        if hasattr(args, key):
            raw = getattr(args, key)
            if raw is None:
                continue
            s = str(raw).strip()
            if s:
                return s
    return None


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ops_log_dir() -> Path:
    return logs_root() / "ops" / "ops_cli"


def _ops_events_path() -> Path:
    return _ops_log_dir() / "ops_cli_events.jsonl"


def _append_ops_event(payload: dict) -> None:
    try:
        path = _ops_events_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        # Never fail ops runs due to logging.
        return


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _ops_latest_dir() -> Path:
    return _ops_log_dir() / "latest"


def _update_ops_latest_pointers(event_finish: dict) -> None:
    """
    Keep-latest pointers for quick "what is the latest run?" lookup.
    Never fails ops runs.
    """
    try:
        _atomic_write_json(_ops_latest_dir() / "latest.json", event_finish)

        ep = event_finish.get("episode")
        episode_id = None
        if isinstance(ep, dict):
            episode_id = str(ep.get("episode_id") or "").strip() or None
        if episode_id:
            safe = episode_id.replace("/", "_")
            _atomic_write_json(_ops_latest_dir() / f"{safe}.json", event_finish)

        cmd = str(event_finish.get("cmd") or "").strip().lower() or None
        if cmd:
            _atomic_write_json(_ops_latest_dir() / f"cmd__{cmd}.json", event_finish)
    except Exception:
        return


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"ops__{stamp}__{secrets.token_hex(4)}"


def _git_info() -> dict | None:
    """
    Best-effort repo version info for "which logic was used".
    Never fails ops runs.
    """
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(_root()),
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
        if sha.returncode != 0:
            return None
        head = (sha.stdout or "").strip()
        if not head:
            return None

        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(_root()),
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
        branch_name = (branch.stdout or "").strip() if branch.returncode == 0 else ""

        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(_root()),
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
        dirty = bool((status.stdout or "").strip()) if status.returncode == 0 else None

        return {
            "head": head,
            "branch": branch_name or None,
            "dirty": dirty,
        }
    except Exception:
        return None


def _actor_info() -> dict:
    agent_name = (os.getenv("LLM_AGENT_NAME") or os.getenv("AGENT_NAME") or "").strip() or None
    user = (os.getenv("USER") or "").strip() or None
    try:
        host = os.uname().nodename
    except Exception:
        host = None
    return {"agent_name": agent_name, "user": user, "host": host}


def _agent_queue_dir() -> Path:
    raw = (os.getenv("LLM_AGENT_QUEUE_DIR") or "").strip()
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else (_root() / p)
    return logs_root() / "agent_tasks"


def _pending_tasks_summary() -> dict | None:
    q = _agent_queue_dir()
    pending_dir = q / "pending"
    if not pending_dir.exists():
        return {"queue_dir": str(q), "count": 0, "ids": []}
    ids: List[str] = []
    try:
        for fp in sorted(pending_dir.glob("*.json")):
            ids.append(fp.stem)
    except Exception:
        ids = []
    return {"queue_dir": str(q), "count": len(ids), "ids": ids[:12]}


def _slack_webhook_url() -> str:
    return str(os.getenv("YTM_SLACK_WEBHOOK_URL") or os.getenv("SLACK_WEBHOOK_URL") or "").strip()


def _slack_bot_token() -> str:
    return str(os.getenv("SLACK_BOT_TOKEN") or os.getenv("YTM_SLACK_BOT_TOKEN") or "").strip()


def _slack_channel() -> str:
    return str(os.getenv("SLACK_CHANNEL") or os.getenv("YTM_SLACK_CHANNEL") or "").strip()


def _slack_notify_configured() -> bool:
    # Webhook or Bot-token mode (token requires channel).
    if _slack_webhook_url():
        return True
    return bool(_slack_bot_token() and _slack_channel())

def _slack_thread_ts() -> str | None:
    """
    Optional: route notifications to a Slack thread to reduce channel spam.
    Requires bot-token mode (webhook cannot reply in a thread).
    """
    ts = str(os.getenv("YTM_SLACK_THREAD_TS") or "").strip()
    if not ts:
        return None
    if not (_slack_bot_token() and _slack_channel()):
        return None
    return ts


def _env_truthy(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on", "y"}


def _ops_tips_enabled() -> bool:
    raw = (os.getenv("YTM_OPS_TIPS") or "").strip()
    if raw == "":
        return True
    return raw.lower() in {"1", "true", "yes", "on", "y"}


def _routing_lockdown_on() -> bool | None:
    raw = (os.getenv("YTM_ROUTING_LOCKDOWN") or "").strip()
    if raw == "":
        return None
    return _env_truthy("YTM_ROUTING_LOCKDOWN")


def _fmt_relpath(p: Path) -> str:
    try:
        return str(p.resolve().relative_to(_root()))
    except Exception:
        return str(p)


def _maybe_print_ops_tips(args: argparse.Namespace, *, llm: str, exec_slot: int | None, run_id: str) -> None:
    """
    Print operator/agent-facing tips to stderr without polluting stdout outputs (tsv/json).
    Controlled by env: YTM_OPS_TIPS (default: on).
    """
    if not _ops_tips_enabled():
        return

    cmd = str(getattr(args, "cmd", "") or "").strip().lower()
    op = str(_op_from_args(args) or "").strip().lower()
    eff = str(llm or "").strip().lower()

    forced = _ops_force_llm_mode()
    default_env_raw = (os.getenv("YTM_OPS_DEFAULT_LLM") or os.getenv("YTM_DEFAULT_LLM") or "").strip()
    default_env = _normalize_llm_mode(default_env_raw) if default_env_raw else None

    lockdown = _routing_lockdown_on()
    lockdown_label = "ON" if lockdown is True else ("OFF" if lockdown is False else "-")
    slot_label = str(exec_slot) if exec_slot is not None else "-"
    emergency_tag = " emergency_override=ON" if _env_truthy("YTM_EMERGENCY_OVERRIDE") else ""

    # Wrapper commands: keep it short (they re-invoke ops).
    if cmd in {"think", "api", "codex"}:
        print(f"[ops] mode wrapper: {cmd.upper()} (run_id={run_id})", file=sys.stderr)
        print("      note: forcing mode for nested command; use `./ops list` for examples", file=sys.stderr)
        return

    mode_tag = eff.upper() if eff else "-"
    forced_tag = f" forced={forced}" if forced else ""
    default_tag = f" default={default_env}" if default_env else ""
    print(
        f"[ops] run_id={run_id} mode={mode_tag} exec_slot={slot_label} lockdown={lockdown_label}{emergency_tag}{forced_tag}{default_tag}",
        file=sys.stderr,
    )

    # Decide whether this command is expected to invoke LLM routing (and thus spend / queue).
    llm_expected = False
    llm_maybe = False
    if cmd in {"script", "audio", "video", "thumbnails", "publish"}:
        llm_expected = True
    elif cmd == "cmd":
        llm_expected = True
        llm_maybe = True
    elif cmd == "resume":
        # resume episode is SSOT-only (no LLM); others may invoke LLM.
        target = str(getattr(args, "target", "") or "").strip().lower()
        llm_expected = target in {"script", "audio", "video", "thumbnails"}
    elif cmd == "reconcile":
        # dry-run is read-only; --run executes fixed resume commands.
        llm_expected = bool(getattr(args, "run", False))
    else:
        llm_expected = False

    if not llm_expected:
        note = "no LLM calls expected"
        if cmd == "reconcile" and not bool(getattr(args, "run", False)):
            note = "dry-run (no resume execution / no LLM calls expected)"
        if cmd == "resume" and op == "episode":
            note = "episode SSOT-only (no LLM calls expected)"
        print(f"[ops] note: {note} for cmd={cmd}{(' op=' + op) if op else ''}.", file=sys.stderr)
        return

    # Script pipeline is API-only (fixed safety rule).
    # Avoid printing generic THINK/CODEX/API tips that would mislead operators.
    is_script = (cmd == "script") or (cmd == "resume" and op == "script")
    if is_script:
        if eff != "api":
            print("[ops] POLICY: script pipeline is API-only (no THINK/CODEX).", file=sys.stderr)
            print("[ops] action: use `./ops api script ...` / `./ops api resume script ...`.", file=sys.stderr)
            return
        print("[ops] API MODE: script pipeline is API-only (no THINK/CODEX).", file=sys.stderr)
        print("[ops] help: `./ops patterns list` / `./ops latest --channel CHxx --video NNN`", file=sys.stderr)
        return

    if eff == "think":
        qdir = _fmt_relpath(_agent_queue_dir())
        print(
            "[ops] THINK MODE: external LLM API spend is disabled; LLM tasks will be queued as pending.",
            file=sys.stderr,
        )
        if llm_maybe:
            print("[ops] note: this depends on what your nested command does (it may not call the LLM).", file=sys.stderr)
        print(f"[ops] next: `./ops agent list`  (queue: {qdir})", file=sys.stderr)
        print("[ops]       `./ops agent prompt <TASK_ID>` → generate output → `./ops agent complete <TASK_ID> ...`", file=sys.stderr)
        print("[ops]       then rerun the same `./ops think ...` command to continue.", file=sys.stderr)
        print("[ops] help: `./ops patterns list` (standard recipes) / `./ops latest --channel CHxx --video NNN` (latest run)", file=sys.stderr)
        return

    if eff == "codex":
        print("[ops] CODEX MODE: tries `codex exec` first, but may fall back to external LLM API for some tasks.", file=sys.stderr)
        print("[ops] tip: if you must guarantee zero external LLM API spend, use `./ops think ...`.", file=sys.stderr)
        print("[ops] help: `./ops patterns list` (standard recipes) / `./ops latest --channel CHxx --video NNN` (latest run)", file=sys.stderr)
        return

    # API mode (default)
    print("[ops] API MODE: this may spend external LLM API (if the command invokes LLM tasks).", file=sys.stderr)
    print("[ops] tip: to avoid spend, use `./ops think ...`.", file=sys.stderr)
    print("[ops] help: `./ops patterns list` (standard recipes) / `./ops latest --channel CHxx --video NNN` (latest run)", file=sys.stderr)

def _slack_notify_cmd_allowlist() -> set[str]:
    raw = (os.getenv("YTM_SLACK_NOTIFY_CMDS") or "").strip()
    if raw:
        return {p.strip() for p in raw.split(",") if p.strip()}
    # Default: only high-value long-running entrypoints (avoid spam).
    return {"script", "audio", "video", "thumbnails", "publish", "resume", "reconcile"}


def _slack_notify_on() -> str:
    raw = (os.getenv("YTM_SLACK_NOTIFY_ON") or "").strip().lower()
    return raw if raw in {"success", "failure", "both"} else "both"


def _slack_notify_min_duration_ms() -> int:
    raw = (os.getenv("YTM_SLACK_NOTIFY_MIN_DURATION_SEC") or "").strip()
    if not raw:
        return 0
    try:
        sec = float(raw)
    except Exception:
        return 0
    return max(0, int(sec * 1000))


def _maybe_slack_notify(event_finish: dict) -> None:
    """
    Best-effort Slack notification on ops finish.
    Opt-in via env:
      - Webhook: YTM_SLACK_WEBHOOK_URL or SLACK_WEBHOOK_URL
      - Bot: SLACK_BOT_TOKEN (+ SLACK_CHANNEL)
    """
    if not _slack_notify_configured():
        return

    cmd = str(event_finish.get("cmd") or "").strip()
    if not cmd:
        return

    if not _env_truthy("YTM_SLACK_NOTIFY_ALL") and cmd not in _slack_notify_cmd_allowlist():
        return

    pending = event_finish.get("pending") if isinstance(event_finish.get("pending"), dict) else {}
    pending_count = int(pending.get("count") or 0) if pending else 0
    duration_ms = int(event_finish.get("duration_ms") or 0) if str(event_finish.get("duration_ms") or "").strip() else 0

    try:
        exit_code = int(event_finish.get("exit_code"))
    except Exception:
        exit_code = 2

    # THINK MODE: pending tasks are not failures; always notify so operators can act.
    if not (str(event_finish.get("llm") or "").strip().lower() == "think" and pending_count > 0):
        on = _slack_notify_on()
        if on == "success" and exit_code != 0:
            return
        if on == "failure" and exit_code == 0:
            return
        if duration_ms < _slack_notify_min_duration_ms():
            return

    try:
        payload = json.dumps(event_finish, ensure_ascii=False)
        cmd = ["python3", "scripts/ops/slack_notify.py", "--event-json", payload]
        thread_ts = _slack_thread_ts()
        if thread_ts:
            cmd += ["--thread-ts", thread_ts]
        subprocess.run(cmd, cwd=str(_root()), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return


def _llm_mode_from_args(args: argparse.Namespace) -> str | None:
    raw = getattr(args, "llm", None)
    if raw is None:
        return None
    s = str(raw).strip().lower()
    return s if s in {"api", "think", "codex"} else None


def _llm_mode_from_argv(argv: List[str]) -> str | None:
    raw = _find_flag_value(argv, "--llm")
    if raw is None:
        return None
    s = str(raw).strip().lower()
    return s if s in {"api", "think", "codex"} else None


def _exec_slot_for_llm(llm: str | None) -> int | None:
    if llm == "think":
        return 3
    if llm == "codex":
        return 1
    if llm == "api":
        return 0
    return None


def _print_list() -> None:
    print("P0 entrypoints (use these; avoid ad-hoc runs):")
    print("")
    print("  One-word mode switch (forces mode for nested ops command):")
    print("    ./ops think <cmd> ...   # force --llm think (no external LLM API spend)")
    print("    ./ops api   <cmd> ...   # force --llm api")
    print("    ./ops codex <cmd> ...   # force --llm codex (explicit)")
    print("    tip: export YTM_OPS_DEFAULT_LLM=think  # make THINK the default when --llm is omitted")
    print("")
    print("  Planning (CSV):")
    print("    ./ops planning lint -- --channel CHxx --write-latest")
    print("    ./ops planning sanitize -- --channel CHxx --write-latest   # dry-run")
    print("    ./ops planning sanitize -- --channel CHxx --apply --write-latest")
    print("")
    print("  Idea cards (pre-planning):")
    print("    ./ops idea help")
    print("")
    print("  Slack / PM (取りこぼし防止):")
    print("    ./ops slack pm-loop -- --channel <C...> --thread-ts <...> --dd-user <U...> --post-digest --process --errors")
    print("")
    print("  Script (runbook):")
    print("    ./ops script <MODE> -- --channel CHxx --video NNN")
    print("    ./ops api script <MODE> -- --channel CHxx --video NNN   # 台本はAPI固定（THINK/CODEX禁止）")
    print("")
    print("  Audio/TTS:")
    print("    ./ops audio -- --channel CHxx --video NNN")
    print("    ./ops audio --llm think -- --channel CHxx --video NNN")
    print("")
    print("  Video / CapCut (SRT→画像→Draft):")
    print("    ./ops video factory -- <args for -m video_pipeline.tools.factory>")
    print("    ./ops video auto-capcut -- <args for -m video_pipeline.tools.auto_capcut_run>")
    print("    ./ops video bootstrap-run -- <args for -m video_pipeline.tools.bootstrap_placeholder_run_dir>")
    print("    ./ops video regen-images -- <args for -m video_pipeline.tools.regenerate_images_from_cues>")
    print("    ./ops video variants -- <args for -m video_pipeline.tools.generate_image_variants>")
    print("    ./ops video refresh-prompts -- <args for -m video_pipeline.tools.refresh_run_prompts>")
    print("    ./ops video audit-fix-drafts -- <args for -m video_pipeline.tools.audit_fix_drafts>")
    print("    ./ops video validate-prompts   # prompt template registry check (P1)")
    print("")
    print("  Thumbnails:")
    print("    ./ops thumbnails help")
    print("    ./ops thumbnails build -- --channel CHxx --videos 001 002 ...")
    print("    ./ops thumbnails retake -- --channel CHxx")
    print("    ./ops thumbnails qc -- --channel CHxx --videos 001 002 ...")
    print("    ./ops thumbnails sync-inventory -- --channel CHxx")
    print("    ./ops thumbnails analyze --all --apply   # benchmark (P1)")
    print("")
    print("  Vision (optional; screenshot/thumb preprocessing):")
    print("    ./ops vision screenshot /path/to/screenshot.png")
    print("    ./ops vision thumbnail /path/to/thumb.png")
    print("")
    print("  UI:")
    print("    ./ops ui start|stop|status")
    print("")
    print("  Publish:")
    print("    ./ops publish -- --max-rows 1 --run --also-lock-local")
    print("")
    print("  Progress / latest view:")
    print("    ./ops progress --channel CHxx --format summary")
    print("    ./ops latest --channel CHxx --video NNN")
    print("    ./ops latest --only-cmd video")
    print("")
    print("  Recovery (fixed commands):")
    print("    ./ops resume episode -- --channel CHxx --video NNN")
    print("    ./ops resume script -- --llm api --channel CHxx --video NNN   # 台本はAPI固定")
    print("    ./ops resume audio -- --llm think --channel CHxx --video NNN")
    print("    ./ops resume video -- --llm think --channel CHxx --video NNN")
    print("    ./ops resume thumbnails -- --llm think --channel CHxx")
    print("    ./ops episode ensure -- --channel CHxx --video NNN   # same as resume episode (explicit)")
    print("")
    print("  Reconcile (stable; dry-run by default):")
    print("    ./ops reconcile --channel CHxx --video NNN")
    print("    ./ops reconcile --channel CHxx --video NNN --llm think --run")
    print("")
    print("  SSOT (latest logic):")
    print("    ./ops ssot status")
    print("    ./ops ssot audit -- --strict")
    print("")
    print("  Cleanup:")
    print("    ./ops cleanup workspace -- --dry-run ...")
    print("    ./ops cleanup logs -- --run")
    print("    ./ops cleanup caches")
    print("    ./ops snapshot workspace -- --write-report")
    print("    ./ops snapshot logs")
    print("")
    print("  Agent queue helpers:")
    print("    ./ops agent list|show|prompt|chat|bundle|claim|complete ...")
    print("")
    print("  Inventories (drift checks):")
    print("    ./ops inventory scripts --write")
    print("    ./ops inventory ssot --check")
    print("")
    print("  Timeline (ops ledger):")
    print("    ./ops history --tail 30")
    print("")
    print("  Execution patterns (SSOT recipes):")
    print("    ./ops patterns list")
    print("    ./ops patterns show PAT-VIDEO-DRAFT-001")
    print("")
    print("Docs:")
    print("  - START_HERE.md")
    print("  - ssot/ops/OPS_ENTRYPOINTS_INDEX.md")


def cmd_list(_args: argparse.Namespace) -> int:
    _print_list()
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    steps = [
        ["python3", "scripts/ops/parallel_ops_preflight.py"],
        ["python3", "scripts/check_env.py"],
    ]
    for s in steps:
        rc = _run(s)
        if rc != 0:
            return rc
    # Soft signal (non-fatal): whether Slack notifications are configured.
    try:
        if _slack_notify_configured():
            mode = "webhook" if _slack_webhook_url() else "bot"
            print(f"[doctor] slack_notify=ON mode={mode}", file=sys.stderr)
        else:
            print("[doctor] slack_notify=OFF (optional)", file=sys.stderr)
    except Exception:
        pass
    # Soft signal (non-fatal): routing + override state (helps prevent silent drift/spend).
    try:
        lockdown = (os.getenv("YTM_ROUTING_LOCKDOWN") or "").strip() or "-"
        emergency = (os.getenv("YTM_EMERGENCY_OVERRIDE") or "").strip() or "-"
        print(f"[doctor] routing_lockdown={lockdown} emergency_override={emergency}", file=sys.stderr)
        # Show effective slots (default vs env) to reduce operator confusion.
        def _effective_model_slot() -> tuple[int, str]:
            raw = (os.getenv("LLM_MODEL_SLOT") or "").strip()
            if raw:
                try:
                    return max(0, int(raw)), "env:LLM_MODEL_SLOT"
                except Exception:
                    pass
            forced_all = (os.getenv("LLM_FORCE_MODELS") or os.getenv("LLM_FORCE_MODEL") or "").strip()
            if forced_all and forced_all.isdigit():
                try:
                    return max(0, int(forced_all)), "env:LLM_FORCE_MODELS"
                except Exception:
                    pass
            try:
                import yaml

                cfg_path = _root() / "configs" / "llm_model_slots.yaml"
                cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
                default_slot = int(cfg.get("default_slot") or 0) if isinstance(cfg, dict) else 0
                return max(0, default_slot), "default"
            except Exception:
                return 0, "default"

        def _effective_exec_slot() -> tuple[int, str]:
            try:
                from factory_common.llm_exec_slots import active_llm_exec_slot_id

                active = active_llm_exec_slot_id()
                return int(active.get("id") or 0), str(active.get("source") or "default")
            except Exception:
                raw = (os.getenv("LLM_EXEC_SLOT") or "").strip()
                if raw:
                    try:
                        return max(0, int(raw)), "env:LLM_EXEC_SLOT"
                    except Exception:
                        pass
                return 0, "default"

        model_slot_id, model_slot_source = _effective_model_slot()
        exec_slot_id, exec_slot_source = _effective_exec_slot()
        print(
            f"[doctor] llm_slots model={model_slot_id} ({model_slot_source}) exec={exec_slot_id} ({exec_slot_source})",
            file=sys.stderr,
        )

        image_override_vars = [
            "IMAGE_CLIENT_FORCE_MODEL_KEY_VISUAL_IMAGE_GEN",
            "IMAGE_CLIENT_FORCE_MODEL_KEY_THUMBNAIL_IMAGE_GEN",
            "IMAGE_CLIENT_FORCE_MODEL_KEY_IMAGE_GENERATION",
            "IMAGE_CLIENT_FORCE_MODEL_KEY",
        ]
        active = []
        for v in image_override_vars:
            val = (os.getenv(v) or "").strip()
            if val:
                active.append(f"{v}={val}")
        if active:
            print("[doctor] WARNING: image model override active (may override preset/template):", file=sys.stderr)
            for line in active:
                print(f"         {line}", file=sys.stderr)
    except Exception:
        pass
    return 0


def cmd_mode(args: argparse.Namespace) -> int:
    mode = _normalize_llm_mode(getattr(args, "mode", None))
    if mode not in {"api", "think", "codex"}:
        print(f"invalid mode: {getattr(args, 'mode', None)} (expected: api|think|codex)", file=sys.stderr)
        return 2

    forwarded = _strip_leading_double_dash(list(getattr(args, "args", []) or []))
    if not forwarded:
        print(f"usage: ./ops {mode} <cmd> ...", file=sys.stderr)
        print("example:", file=sys.stderr)
        print(f"  ./ops {mode} audio -- --channel CHxx --video NNN", file=sys.stderr)
        return 2

    env = dict(os.environ)
    env["YTM_OPS_FORCE_LLM"] = str(mode)
    # Run the same entrypoint with the forced mode applied.
    inner = [sys.executable, "scripts/ops/ops_cli.py", *forwarded]
    return _run(inner, env=env)


def _llm_mode_slot(llm: str) -> int:
    """
    Exec-slot mapping (SSOT: configs/llm_exec_slots.yaml):
      - api   -> 0 (API only; codex exec OFF under lockdown)
      - codex -> 1 (API + codex exec forced ON)
      - think -> 3 (agent queue; no external LLM API spend)
    """
    llm = str(llm or "").strip().lower()
    if llm == "think":
        return 3
    if llm == "codex":
        return 1
    return 0


def _run_with_llm_mode(llm: str, inner_cmd: List[str]) -> int:
    llm = _apply_forced_llm(llm)
    if llm == "think":
        return _run_think(inner_cmd)
    forced_exec_slot = _ops_force_exec_slot()
    if forced_exec_slot is not None:
        return _run(inner_cmd, env=_env_with_llm_exec_slot(forced_exec_slot))
    slot = _llm_mode_slot(llm)
    return _run(inner_cmd, env=_env_with_llm_exec_slot(slot))


def cmd_cmd(args: argparse.Namespace) -> int:
    forwarded = _strip_leading_double_dash(list(args.command or []))
    if not forwarded:
        print("missing command. Use: ./ops cmd -- <command> [args...]", file=sys.stderr)
        return 2
    return _run_with_llm_mode(args.llm, forwarded)


def cmd_script(args: argparse.Namespace) -> int:
    llm = _apply_forced_llm(args.llm)
    if llm != "api":
        print("[POLICY] script pipeline is API-only (no THINK/CODEX).", file=sys.stderr)
        print("- rule: 台本（script_*）は LLM API（Fireworks）固定。Codex/agent 代行で台本を書かない。", file=sys.stderr)
        print("- action: rerun with `./ops api script ...` (or `--llm api`)", file=sys.stderr)
        return 2
    # Policy: never run the script pipeline with an ops-level exec-slot override.
    if _ops_force_exec_slot() not in (None, 0):
        print("[ops] NOTE: ignoring --exec-slot for script pipeline (API-only; exec_slot forced to 0).", file=sys.stderr)
    forwarded = _strip_leading_double_dash(list(args.args))
    inner = ["python3", "scripts/ops/script_runbook.py", args.mode, *forwarded]
    return _run(inner, env=_env_with_llm_exec_slot(0))


def cmd_audio(args: argparse.Namespace) -> int:
    forwarded = _strip_leading_double_dash(list(args.args))
    inner = ["python3", "-m", "script_pipeline.cli", "audio", *forwarded]
    return _run_with_llm_mode(args.llm, inner)


def cmd_publish(args: argparse.Namespace) -> int:
    forwarded = _strip_leading_double_dash(list(args.args))
    inner = ["python3", "scripts/youtube_publisher/publish_from_sheet.py", *forwarded]
    return _run_with_llm_mode(args.llm, inner)


def cmd_ui(args: argparse.Namespace) -> int:
    inner = ["bash", "scripts/start_all.sh", args.action]
    return _run(inner)


def cmd_agent(args: argparse.Namespace) -> int:
    forwarded = _strip_leading_double_dash(list(args.args))
    inner = ["python3", "scripts/agent_runner.py", *forwarded]
    return _run(inner)


def cmd_slack(args: argparse.Namespace) -> int:
    """
    Slack helpers.
    NOTE: Slack operations are orthogonal to LLM mode; this wrapper does not set exec-slot.
    """
    forwarded = _strip_leading_double_dash(list(args.args))
    action = str(args.action or "").strip()
    if action == "pm-loop":
        inner = ["python3", "scripts/ops/slack_pm_loop.py", "run", *forwarded]
        return _run(inner)
    print(f"unknown slack action: {action}", file=sys.stderr)
    return 2


def cmd_planning_lint(args: argparse.Namespace) -> int:
    inner = ["python3", "scripts/ops/planning_lint.py", *args.args]
    return _run_with_llm_mode(args.llm, inner)


def cmd_planning_sanitize(args: argparse.Namespace) -> int:
    inner = ["python3", "scripts/ops/planning_sanitize.py", *args.args]
    return _run_with_llm_mode(args.llm, inner)


def cmd_planning(args: argparse.Namespace) -> int:
    forwarded = _strip_leading_double_dash(list(args.args))
    if args.action == "lint":
        inner = ["python3", "scripts/ops/planning_lint.py", *forwarded]
        return _run_with_llm_mode(args.llm, inner)
    if args.action == "sanitize":
        inner = ["python3", "scripts/ops/planning_sanitize.py", *forwarded]
        return _run_with_llm_mode(args.llm, inner)
    print(f"unknown planning action: {args.action}", file=sys.stderr)
    return 2


def cmd_idea(args: argparse.Namespace) -> int:
    if args.action == "help":
        inner = ["python3", "scripts/ops/idea.py", "--help"]
        return _run(inner)
    forwarded = _strip_leading_double_dash(list(args.args))
    inner = ["python3", "scripts/ops/idea.py", args.action, *forwarded]
    return _run_with_llm_mode(args.llm, inner)


def cmd_video_auto_capcut(args: argparse.Namespace) -> int:
    inner = ["python3", "-m", "video_pipeline.tools.auto_capcut_run", *args.args]
    return _run_with_llm_mode(args.llm, inner)


def cmd_video_regen_images(args: argparse.Namespace) -> int:
    inner = ["python3", "-m", "video_pipeline.tools.regenerate_images_from_cues", *args.args]
    return _run_with_llm_mode(args.llm, inner)


def cmd_video(args: argparse.Namespace) -> int:
    forwarded = _strip_leading_double_dash(list(args.args))
    action = str(args.action or "").strip()
    if action == "factory":
        inner = ["python3", "-m", "video_pipeline.tools.factory", *forwarded]
        return _run_with_llm_mode(args.llm, inner)
    if action == "auto-capcut":
        inner = ["python3", "-m", "video_pipeline.tools.auto_capcut_run", *forwarded]
        return _run_with_llm_mode(args.llm, inner)
    if action == "bootstrap-run":
        inner = ["python3", "-m", "video_pipeline.tools.bootstrap_placeholder_run_dir", *forwarded]
        return _run_with_llm_mode(args.llm, inner)
    if action == "regen-images":
        inner = ["python3", "-m", "video_pipeline.tools.regenerate_images_from_cues", *forwarded]
        return _run_with_llm_mode(args.llm, inner)
    if action == "variants":
        inner = ["python3", "-m", "video_pipeline.tools.generate_image_variants", *forwarded]
        return _run(inner)
    if action == "refresh-prompts":
        inner = ["python3", "-m", "video_pipeline.tools.refresh_run_prompts", *forwarded]
        return _run_with_llm_mode(args.llm, inner)
    if action == "audit-fix-drafts":
        inner = ["python3", "-m", "video_pipeline.tools.audit_fix_drafts", *forwarded]
        return _run_with_llm_mode(args.llm, inner)
    if action == "validate-prompts":
        inner = ["python3", "-m", "video_pipeline.tools.validate_prompt_template_registry", *forwarded]
        return _run(inner)
    if action == "apply-source-mix":
        inner = ["python3", "-m", "video_pipeline.tools.apply_image_source_mix", *forwarded]
        return _run(inner)
    print(f"unknown video action: {action}", file=sys.stderr)
    return 2


def cmd_thumbnails(args: argparse.Namespace) -> int:
    forwarded = _strip_leading_double_dash(list(args.args))
    action = str(args.action or "").strip()
    if action in {"build", "retake", "qc"}:
        inner = ["python3", "scripts/thumbnails/build.py", action, *forwarded]
        return _run_with_llm_mode(args.llm, inner)
    if action == "sync-inventory":
        inner = ["python3", "scripts/sync_thumbnail_inventory.py", *forwarded]
        return _run(inner)
    if action == "analyze":
        inner = ["python3", "scripts/ops/yt_dlp_thumbnail_analyze.py", *forwarded]
        return _run_with_llm_mode(args.llm, inner)
    if action == "help":
        inner = ["python3", "scripts/thumbnails/build.py", "--help"]
        return _run(inner)
    print(f"unknown thumbnails action: {action}", file=sys.stderr)
    return 2


def cmd_vision(args: argparse.Namespace) -> int:
    forwarded = _strip_leading_double_dash(list(args.args))
    action = str(args.action or "").strip()
    if action == "help":
        inner = ["python3", "scripts/vision/vision_pack.py", "--help"]
        return _run(inner)
    inner = ["python3", "scripts/vision/vision_pack.py", action, *forwarded]
    return _run(inner)


def cmd_progress(args: argparse.Namespace) -> int:
    inner = ["python3", "scripts/ops/episode_progress.py", "--channel", args.channel]
    if args.videos:
        inner += ["--videos", args.videos]
    if args.format:
        inner += ["--format", args.format]
    if args.issues_only:
        inner.append("--issues-only")
    if args.include_unplanned:
        inner.append("--include-unplanned")
    if args.include_hidden_runs:
        inner.append("--include-hidden-runs")
    return _run(inner)


def cmd_cleanup_workspace(args: argparse.Namespace) -> int:
    inner = ["python3", "-m", "scripts.cleanup_workspace", *args.args]
    return _run(inner)


def cmd_cleanup_logs(args: argparse.Namespace) -> int:
    inner = ["python3", "scripts/ops/cleanup_logs.py", *args.args]
    return _run(inner)


def cmd_cleanup_caches(_args: argparse.Namespace) -> int:
    inner = ["bash", "scripts/ops/cleanup_caches.sh"]
    return _run(inner)


def cmd_cleanup(args: argparse.Namespace) -> int:
    forwarded = _strip_leading_double_dash(list(args.args))
    action = str(args.action or "").strip()
    if action == "workspace":
        inner = ["python3", "-m", "scripts.cleanup_workspace", *forwarded]
        return _run(inner)
    if action == "logs":
        inner = ["python3", "scripts/ops/cleanup_logs.py", *forwarded]
        return _run(inner)
    if action == "caches":
        inner = ["bash", "scripts/ops/cleanup_caches.sh"]
        return _run(inner)
    print(f"unknown cleanup action: {action}", file=sys.stderr)
    return 2


def cmd_snapshot(args: argparse.Namespace) -> int:
    forwarded = _strip_leading_double_dash(list(args.args))
    action = str(args.action or "").strip()
    if action == "workspace":
        inner = ["python3", "scripts/ops/workspace_snapshot.py", *forwarded]
        return _run(inner)
    if action == "logs":
        inner = ["python3", "scripts/ops/logs_snapshot.py", *forwarded]
        return _run(inner)
    print(f"unknown snapshot action: {action}", file=sys.stderr)
    return 2


def cmd_history(args: argparse.Namespace) -> int:
    path = _ops_events_path()
    if not path.exists():
        print(f"(no ops history yet; missing: {path})")
        return 0

    by_id: dict[str, dict] = {}
    order: list[str] = []
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            run_id = str(obj.get("run_id") or "").strip()
            if not run_id:
                continue
            ev = str(obj.get("event") or "").strip()
            if run_id not in by_id:
                by_id[run_id] = {"run_id": run_id}
                order.append(run_id)
            rec = by_id[run_id]
            if ev == "start":
                rec["started_at"] = obj.get("at")
                rec["cmd"] = obj.get("cmd")
                rec["op"] = obj.get("op")
                rec["llm"] = obj.get("llm")
                rec["exec_slot"] = obj.get("exec_slot")
                ep = obj.get("episode")
                if isinstance(ep, dict):
                    rec["channel"] = ep.get("channel")
                    rec["video"] = ep.get("video")
                    rec["videos"] = ep.get("videos")
                    rec["episode_id"] = ep.get("episode_id")
                    rec["run_dir"] = ep.get("run_dir")
            elif ev == "finish":
                rec["finished_at"] = obj.get("at")
                rec["exit_code"] = obj.get("exit_code")
                rec["duration_ms"] = obj.get("duration_ms")

    channel_filter = _normalize_channel(getattr(args, "channel", None))
    video_filter = _normalize_video(getattr(args, "video", None))
    only_cmd = str(getattr(args, "only_cmd", "") or "").strip() or None
    failed_only = bool(getattr(args, "failed_only", False))

    def _match(r: dict) -> bool:
        if only_cmd and str(r.get("cmd") or "") != only_cmd:
            return False
        if channel_filter and str(r.get("channel") or "") != channel_filter:
            return False
        if video_filter:
            if str(r.get("video") or "") == video_filter:
                pass
            else:
                vids = r.get("videos")
                if not (isinstance(vids, list) and video_filter in [str(x) for x in vids]):
                    return False
        if failed_only:
            code = r.get("exit_code")
            if code is None:
                return False
            try:
                if int(code) == 0:
                    return False
            except Exception:
                # Non-integer/unknown -> treat as failure-ish
                pass
        return True

    def _episode_label(r: dict) -> str:
        eid = str(r.get("episode_id") or "").strip()
        if eid:
            return eid
        ch = str(r.get("channel") or "").strip()
        vids = r.get("videos")
        if ch and isinstance(vids, list) and vids:
            return f"{ch}:{','.join([str(v) for v in vids])}"
        if ch:
            return ch
        run_dir = str(r.get("run_dir") or "").strip()
        if run_dir:
            return Path(run_dir).name
        return "-"

    selected: List[str] = []
    for rid in order:
        rec = by_id.get(rid) or {}
        if _match(rec):
            selected.append(rid)

    tail = int(args.tail or 30)
    print("started_at\texit\tduration_ms\tllm\tcmd\top\tepisode\trun_id")
    for rid in selected[-tail:]:
        rec = by_id.get(rid) or {}
        row = [
            str(rec.get("started_at") or "-"),
            str(rec.get("exit_code") if rec.get("exit_code") is not None else "-"),
            str(rec.get("duration_ms") if rec.get("duration_ms") is not None else "-"),
            str(rec.get("llm") or "-"),
            str(rec.get("cmd") or "-"),
            str(rec.get("op") or "-"),
            _episode_label(rec),
            str(rec.get("run_id") or "-"),
        ]
        print("\t".join(row))
    return 0


_PATTERNS_DOC_REL = Path("ssot") / "ops" / "OPS_EXECUTION_PATTERNS.md"
_PATTERN_HEADER_RE = re.compile(r"^##\s+(PAT-[A-Z0-9][A-Z0-9_-]*)(?:\s+—\s+|\s+-\s+)(.+?)\s*$")


def _patterns_doc_path() -> Path:
    return _root() / _PATTERNS_DOC_REL


def _scan_execution_patterns(lines: list[str]) -> list[dict]:
    """
    Parse `## PAT-... — <title>` headings from the SSOT patterns doc.
    Intentionally ignores code-fenced template blocks.
    """
    in_fence = False
    out: list[dict] = []
    for idx, raw in enumerate(lines):
        line = raw.rstrip("\n")
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _PATTERN_HEADER_RE.match(line)
        if not m:
            continue
        out.append({"id": m.group(1), "title": m.group(2), "line": idx + 1})
    return out


def cmd_patterns(args: argparse.Namespace) -> int:
    action = str(getattr(args, "action", "") or "").strip().lower()
    path = _patterns_doc_path()
    if not path.exists():
        print(f"missing patterns SSOT: {path}", file=sys.stderr)
        return 2

    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    pats = _scan_execution_patterns(lines)

    if action in {"help", ""}:
        print("Execution patterns (SSOT recipes):")
        print("")
        print(f"  source: {_fmt_relpath(path)}")
        print("")
        print("  list:")
        print("    ./ops patterns list")
        print("    ./ops patterns list --grep video")
        print("")
        print("  show:")
        print("    ./ops patterns show PAT-VIDEO-DRAFT-001")
        return 0

    if action == "list":
        grep = str(getattr(args, "grep", "") or "").strip().lower()
        selected = []
        for p in pats:
            if not grep:
                selected.append(p)
                continue
            if grep in str(p.get("id") or "").lower() or grep in str(p.get("title") or "").lower():
                selected.append(p)

        if bool(getattr(args, "json", False)):
            print(json.dumps(selected, ensure_ascii=False))
            return 0

        print("pattern_id\ttitle\tline")
        for p in selected:
            print(f"{p.get('id')}\t{p.get('title')}\t{p.get('line')}")
        return 0

    if action == "show":
        pid = str(getattr(args, "pattern_id", "") or "").strip()
        if not pid:
            print("usage: ./ops patterns show PAT-...", file=sys.stderr)
            return 2

        start_idx: int | None = None
        for p in pats:
            if str(p.get("id") or "").strip() == pid:
                start_idx = int(p.get("line") or 1) - 1
                break
        if start_idx is None:
            print(f"pattern not found: {pid}", file=sys.stderr)
            print("try: ./ops patterns list --grep <keyword>", file=sys.stderr)
            return 2

        # Print from this header until the next pattern header (or EOF).
        end_idx = len(lines)
        for j in range(start_idx + 1, len(lines)):
            if _PATTERN_HEADER_RE.match(lines[j]):
                end_idx = j
                break
        print("\n".join(lines[start_idx:end_idx]).rstrip() + "\n")
        return 0

    print(f"unknown patterns action: {action} (expected: list|show|help)", file=sys.stderr)
    return 2


def cmd_latest(args: argparse.Namespace) -> int:
    """
    Show keep-latest pointers written by ops_cli.
    This is a convenience reader for "what happened most recently?".
    """
    latest_dir = _ops_latest_dir()
    if not latest_dir.exists():
        print(f"(no latest pointers yet; missing: {latest_dir})")
        return 0

    only_cmd = str(getattr(args, "only_cmd", "") or "").strip().lower()
    channel = _normalize_channel(getattr(args, "channel", None))
    video = _normalize_video(getattr(args, "video", None))

    if only_cmd:
        path = latest_dir / f"cmd__{only_cmd}.json"
    elif channel and video:
        path = latest_dir / f"{channel}-{video}.json"
    else:
        path = latest_dir / "latest.json"

    if not path.exists():
        print(f"(missing: {path})")
        return 0

    raw = path.read_text(encoding="utf-8", errors="ignore").strip()
    if bool(getattr(args, "json", False)):
        print(raw)
        return 0

    try:
        obj = json.loads(raw) if raw else {}
    except Exception:
        obj = {}

    started_at = str(obj.get("started_at") or obj.get("at") or "-")
    finished_at = str(obj.get("at") or "-")
    exit_code = obj.get("exit_code")
    duration_ms = obj.get("duration_ms")
    llm = str(obj.get("llm") or "-")
    cmd_name = str(obj.get("cmd") or "-")
    op = str(obj.get("op") or "-")
    run_id = str(obj.get("run_id") or "-")
    actor = obj.get("actor") if isinstance(obj.get("actor"), dict) else {}
    agent_name = str(actor.get("agent_name") or "-") if isinstance(actor, dict) else "-"
    episode = obj.get("episode") if isinstance(obj.get("episode"), dict) else {}
    episode_id = str(episode.get("episode_id") or "-") if isinstance(episode, dict) else "-"
    run_dir = str(episode.get("run_dir") or "-") if isinstance(episode, dict) else "-"

    print("started_at\tfinished_at\texit\tduration_ms\tllm\tcmd\top\tepisode\tagent\trun_dir\trun_id")
    print(
        "\t".join(
            [
                started_at,
                finished_at,
                str(exit_code if exit_code is not None else "-"),
                str(duration_ms if duration_ms is not None else "-"),
                llm,
                cmd_name,
                op,
                episode_id,
                agent_name,
                run_dir,
                run_id,
            ]
        )
    )
    return 0


def cmd_inventory(args: argparse.Namespace) -> int:
    kind = args.kind
    if kind == "scripts":
        inner = ["python3", "scripts/ops/scripts_inventory.py", "--write" if args.write else "--stdout"]
        return _run(inner)
    if kind == "ssot":
        inner = ["python3", "scripts/ops/build_ssot_catalog.py", "--check" if args.check else "--write"]
        return _run(inner)
    if kind == "docs":
        inner = ["python3", "scripts/ops/docs_inventory.py"]
        if args.write:
            inner.append("--write")
        return _run(inner)
    print(f"unknown inventory kind: {kind}", file=sys.stderr)
    return 2


def cmd_episode(args: argparse.Namespace) -> int:
    forwarded = _strip_leading_double_dash(list(args.args))
    if args.action == "help":
        return _run(["python3", "scripts/episode_ssot.py", "--help"])
    inner = ["python3", "scripts/episode_ssot.py", args.action, *forwarded]
    return _run(inner)


def cmd_resume(args: argparse.Namespace) -> int:
    forwarded = _strip_leading_double_dash(list(args.args))
    doctor, forwarded = _extract_doctor_flag(forwarded)
    llm, forwarded = _extract_llm_flag(forwarded)

    if doctor:
        rc = cmd_doctor(argparse.Namespace())
        if rc != 0:
            return rc

    target = str(args.target or "").strip().lower()
    if target == "episode":
        global _OPS_EXIT_CODE_RAW, _OPS_LAST_FAILED_RUN, _OPS_LAST_WARN_RUN
        channel = _normalize_channel(_find_flag_value(forwarded, "--channel"))
        video = _normalize_video(_find_flag_value(forwarded, "--video"))
        inner = ["python3", "scripts/episode_ssot.py", "ensure", *forwarded]
        rc = _run(inner)
        # episode_ssot.py uses exit=2 to signal WARN (manifest warnings). Treat it as non-fatal at the ops entrypoint.
        if rc == 2 and channel and video:
            _OPS_EXIT_CODE_RAW = 2
            # Move the captured log into WARN bucket (avoid "FAILED" summary downstream).
            if _OPS_LAST_FAILED_RUN is not None:
                _OPS_LAST_WARN_RUN = _OPS_LAST_FAILED_RUN
                _OPS_LAST_FAILED_RUN = None
            manifest_path = _root() / "workspaces" / "episodes" / channel / video / "episode_manifest.json"
            warnings: List[str] = []
            try:
                if manifest_path.exists():
                    obj = json.loads(manifest_path.read_text(encoding="utf-8"))
                    raw = obj.get("warnings") if isinstance(obj, dict) else None
                    if isinstance(raw, list):
                        warnings = [str(x) for x in raw]
            except Exception:
                warnings = []
            _set_ops_warnings(warnings=warnings, manifest_path=manifest_path, note="episode_manifest warnings (non-fatal)")
            return 0
        return rc

    if target == "script":
        eff = _apply_forced_llm(llm)
        if eff != "api":
            print("[POLICY] resume script is API-only (no THINK/CODEX).", file=sys.stderr)
            print("- rule: 台本（script_*）は LLM API（Fireworks）固定。Codex/agent 代行で台本を書かない。", file=sys.stderr)
            print("- action: rerun with `./ops api resume script ...` (or add `--llm api`)", file=sys.stderr)
            return 2
        if _ops_force_exec_slot() not in (None, 0):
            print("[ops] NOTE: ignoring --exec-slot for script pipeline (API-only; exec_slot forced to 0).", file=sys.stderr)
        inner = ["python3", "scripts/ops/script_runbook.py", "resume", *forwarded]
        return _run(inner, env=_env_with_llm_exec_slot(0))

    if target == "audio":
        inner = ["python3", "-m", "script_pipeline.cli", "audio", *forwarded]
        return _run_with_llm_mode(llm, inner)

    if target == "video":
        channel = _normalize_channel(_find_flag_value(forwarded, "--channel"))
        video = _normalize_video(_find_flag_value(forwarded, "--video"))
        if not channel or not video:
            print("resume video requires --channel CHxx --video NNN", file=sys.stderr)
            return 2

        try:
            srt_path = _resolve_final_srt_path(channel=channel, video=video)
        except FileNotFoundError as exc:
            print(str(exc), file=sys.stderr)
            return 2

        factory_args = list(forwarded)
        factory_args = _drop_flag_with_value(factory_args, "--channel")
        factory_args = _drop_flag_with_value(factory_args, "--video")
        factory_args = _drop_flag(factory_args, "--include-hidden-runs")
        factory_args = _drop_flag(factory_args, "--sync-mirror")
        factory_args = _drop_flag(factory_args, "--confirm-a")
        factory_args = _drop_flag_with_value(factory_args, "--videos")

        inner = ["python3", "-m", "video_pipeline.tools.factory", channel, str(srt_path), "draft", *factory_args]
        rc = _run_with_llm_mode(llm, inner)
        if rc != 0:
            return rc

        ensure_cmd = ["python3", "scripts/episode_ssot.py", "ensure", "--channel", channel, "--video", video]
        if "--include-hidden-runs" in forwarded:
            ensure_cmd.append("--include-hidden-runs")
        if "--confirm-a" in forwarded:
            ensure_cmd.append("--confirm-a")
        if "--sync-mirror" in forwarded:
            ensure_cmd.append("--sync-mirror")
        return _run(ensure_cmd)

    if target == "thumbnails":
        inner = ["python3", "scripts/thumbnails/build.py", "retake", *forwarded]
        return _run_with_llm_mode(llm, inner)

    print(f"unknown resume target: {target}", file=sys.stderr)
    return 2


def _parse_videos_csv(video: str, videos: str) -> List[str] | None:
    raw: List[str] = []
    if str(video or "").strip():
        raw.append(str(video))
    if str(videos or "").strip():
        raw.extend([p.strip() for p in str(videos).split(",") if p.strip()])

    out: List[str] = []
    for token in raw:
        norm = _normalize_video(token)
        if norm:
            out.append(norm)
    out = sorted(set(out))
    return out or None


def cmd_reconcile(args: argparse.Namespace) -> int:
    """
    Deterministic, stable "reconcile" entrypoint.
    Default is dry-run; use --run to execute.
    """
    channel = _normalize_channel(getattr(args, "channel", None))
    if not channel:
        print("reconcile requires --channel CHxx", file=sys.stderr)
        return 2

    videos = _parse_videos_csv(str(getattr(args, "video", "") or ""), str(getattr(args, "videos", "") or ""))
    if bool(getattr(args, "run", False)) and not videos and not bool(getattr(args, "all", False)):
        print("refusing to --run without --video/--videos (use --all to override)", file=sys.stderr)
        return 2
    include_unplanned = bool(getattr(args, "include_unplanned", False))
    include_hidden_runs = bool(getattr(args, "include_hidden_runs", False))

    from factory_common.episode_progress import build_episode_progress_view

    view = build_episode_progress_view(
        channel,
        videos=videos,
        include_unplanned=include_unplanned,
        include_hidden_runs=include_hidden_runs,
    )
    episodes = view.get("episodes") or []

    llm = _apply_forced_llm(str(getattr(args, "llm", "") or ""))
    if llm not in {"api", "think", "codex"}:
        print(f"invalid --llm: {llm}", file=sys.stderr)
        return 2

    pass_hidden = ["--include-hidden-runs"] if include_hidden_runs else []
    pass_confirm_a = ["--confirm-a"] if bool(getattr(args, "confirm_a", False)) else []
    pass_sync_mirror = ["--sync-mirror"] if bool(getattr(args, "sync_mirror", False)) else []

    planned: List[dict] = []
    notes: List[str] = []

    for ep in episodes:
        issues = [str(x) for x in (ep.get("issues") or []) if str(x).strip()]
        if not issues:
            continue
        vid = str(ep.get("video") or "").strip()
        if not vid:
            continue
        episode_id = str(ep.get("episode_id") or f"{channel}-{vid}")
        audio_ready = bool(ep.get("audio_ready"))

        capcut_issue = any(i in issues for i in ("capcut_draft_missing", "capcut_draft_broken"))
        run_issue = any(i in issues for i in ("video_run_unselected", "video_run_missing"))
        status_missing = "status_json_missing" in issues
        planning_stale = "planning_stale_vs_status" in issues
        planning_dupe = "planning_duplicate_video_rows" in issues

        if planning_dupe:
            notes.append(f"{episode_id}\tplanning_duplicate_video_rows\tmanual_fix\t(planning CSV has duplicate rows)")
        if planning_stale:
            notes.append(f"{episode_id}\tplanning_stale_vs_status\tmanual_fix\t(update planning CSV progress)")
        if status_missing:
            notes.append(f"{episode_id}\tstatus_json_missing\tmanual_fix\t(run validate_status_sweep / restore status.json)")

        if capcut_issue:
            if not audio_ready:
                planned.append(
                    {
                        "episode": episode_id,
                        "target": "audio",
                        "reason": "audio_ready=false (required for video draft)",
                        "args": ["--skip-doctor", "--llm", llm, "--channel", channel, "--video", vid],
                    }
                )
            planned.append(
                {
                    "episode": episode_id,
                    "target": "video",
                    "reason": "capcut_draft_missing/broken",
                    "args": [
                        "--skip-doctor",
                        "--llm",
                        llm,
                        "--channel",
                        channel,
                        "--video",
                        vid,
                        *pass_hidden,
                        *pass_confirm_a,
                        *pass_sync_mirror,
                    ],
                }
            )
            continue

        if run_issue:
            planned.append(
                {
                    "episode": episode_id,
                    "target": "episode",
                    "reason": "video_run_unselected/missing",
                    "args": [
                        "--skip-doctor",
                        "--llm",
                        llm,
                        "--channel",
                        channel,
                        "--video",
                        vid,
                        *pass_hidden,
                        *pass_confirm_a,
                        *pass_sync_mirror,
                    ],
                }
            )

    if planned:
        print("episode\taction\tllm\treason")
        for item in planned:
            print(f"{item.get('episode')}\tresume {item.get('target')}\t{llm}\t{item.get('reason')}")
    else:
        print("(no reconcile actions needed)")

    if notes:
        print("")
        print("episode\tissue\tmode\tnote")
        for line in notes:
            print(line)

    if not bool(getattr(args, "run", False)):
        if planned:
            print("")
            print("dry-run (use --run to execute)")
        return 0

    if not bool(getattr(args, "skip_doctor", False)):
        rc = cmd_doctor(argparse.Namespace())
        if rc != 0:
            return rc

    continue_on_error = bool(getattr(args, "continue_on_error", False))
    for item in planned:
        rc = cmd_resume(argparse.Namespace(target=str(item.get("target")), args=list(item.get("args") or [])))
        if rc != 0 and not continue_on_error:
            return int(rc)
    return 0


def cmd_ssot(args: argparse.Namespace) -> int:
    action = str(args.action or "").strip().lower()
    forwarded = _strip_leading_double_dash(list(args.args))

    if action == "status":
        git = _git_info() or {}

        def mtime_utc(p: Path) -> str:
            try:
                ts = p.stat().st_mtime
            except Exception:
                return "-"
            return datetime.fromtimestamp(ts, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

        anchors = [
            "START_HERE.md",
            "ssot/README.md",
            "ssot/DOCS_INDEX.md",
            "ssot/reference/【消さないで！人間用】確定ロジック.md",
            "ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md",
            "ssot/ops/OPS_ENTRYPOINTS_INDEX.md",
            "ssot/ops/OPS_EXECUTION_PATTERNS.md",
            "ssot/ops/OPS_FIXED_RECOVERY_COMMANDS.md",
            "ssot/ops/OPS_RECONCILE_RUNBOOK.md",
        ]

        print(f"repo_root\t{_root()}")
        if git:
            print(f"git_head\t{git.get('head') or '-'}")
            print(f"git_branch\t{git.get('branch') or '-'}")
            dirty = git.get("dirty")
            print(f"git_dirty\t{('1' if dirty else '0') if dirty is not None else '-'}")
        else:
            print("git_head\t-")
            print("git_branch\t-")
            print("git_dirty\t-")

        print("")
        print("anchor\tmtime_utc\texists")
        for rel in anchors:
            p = _root() / rel
            exists = p.exists()
            print(f"{rel}\t{mtime_utc(p) if exists else '-'}\t{1 if exists else 0}")
        return 0

    if action == "audit":
        inner = ["python3", "scripts/ops/ssot_audit.py", "--path-audit", "--link-audit", *forwarded]
        return _run(inner)

    if action == "check":
        inner = ["python3", "scripts/ops/pre_push_final_check.py", *forwarded]
        return _run(inner)

    print(f"unknown ssot action: {action}", file=sys.stderr)
    return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ops", description="Unified operator entrypoint (P0 launcher)")
    p.add_argument(
        "--llm-slot",
        dest="llm_slot",
        type=int,
        default=None,
        help="Set LLM_MODEL_SLOT for this ops run (numeric model slot).",
    )
    p.add_argument(
        "--exec-slot",
        dest="exec_slot",
        type=int,
        default=None,
        help="Override exec-slot for this ops run (advanced; prefer ./ops api|think|codex).",
    )
    p.add_argument(
        "--emergency-override",
        dest="emergency_override",
        action="store_true",
        help="Set YTM_EMERGENCY_OVERRIDE=1 for this ops run (debug only).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("list", help="show P0 entrypoints")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("doctor", help="run preflight/env checks")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("think", help="force THINK MODE for the nested ops command")
    sp.add_argument("args", nargs=argparse.REMAINDER, help="nested ops command (use '--' before flags; e.g. audio -- --channel CHxx --video NNN)")
    sp.set_defaults(func=cmd_mode, mode="think")

    sp = sub.add_parser("api", help="force API mode for the nested ops command")
    sp.add_argument("args", nargs=argparse.REMAINDER, help="nested ops command (use '--' before flags; e.g. audio -- --channel CHxx --video NNN)")
    sp.set_defaults(func=cmd_mode, mode="api")

    sp = sub.add_parser("codex", help="force Codex exec mode for the nested ops command (explicit)")
    sp.add_argument("args", nargs=argparse.REMAINDER, help="nested ops command (use '--' before flags; e.g. audio -- --channel CHxx --video NNN)")
    sp.set_defaults(func=cmd_mode, mode="codex")

    sp = sub.add_parser("cmd", help="run an arbitrary command with selected LLM mode")
    sp.add_argument("--llm", choices=["api", "think", "codex"], default=_ops_default_llm_mode())
    sp.add_argument("command", nargs=argparse.REMAINDER, help="command after '--'")
    sp.set_defaults(func=cmd_cmd)

    sp = sub.add_parser("script", help="script pipeline runbook wrapper")
    sp.add_argument("--llm", choices=["api", "think", "codex"], default=_ops_default_llm_mode())
    sp.add_argument("mode", help="runbook mode (new/redo-full/resume/rewrite/seed-expand)")
    sp.add_argument("args", nargs=argparse.REMAINDER, help="args passed to scripts/ops/script_runbook.py (use '--' before flags)")
    sp.set_defaults(func=cmd_script)

    sp = sub.add_parser("audio", help="audio/TTS wrapper (script_pipeline.cli audio)")
    sp.add_argument("--llm", choices=["api", "think", "codex"], default=_ops_default_llm_mode())
    sp.add_argument("args", nargs=argparse.REMAINDER, help="args passed to python -m script_pipeline.cli audio (use '--' before flags)")
    sp.set_defaults(func=cmd_audio)

    sp = sub.add_parser("publish", help="publish wrapper (publish_from_sheet.py)")
    sp.add_argument("--llm", choices=["api", "think", "codex"], default=_ops_default_llm_mode())
    sp.add_argument("args", nargs=argparse.REMAINDER, help="args passed to publish_from_sheet.py (use '--' before flags)")
    sp.set_defaults(func=cmd_publish)

    sp = sub.add_parser("planning", help="planning helpers (lint/sanitize)")
    sp.add_argument("--llm", choices=["api", "think", "codex"], default=_ops_default_llm_mode())
    sp.add_argument("action", choices=["lint", "sanitize"], help="planning operation")
    sp.add_argument("args", nargs=argparse.REMAINDER, help="args passed to the underlying planning tool (use '--' before flags)")
    sp.set_defaults(func=cmd_planning)

    sp = sub.add_parser("idea", help="idea cards manager (scripts/ops/idea.py)")
    sp.add_argument("--llm", choices=["api", "think", "codex"], default=_ops_default_llm_mode())
    sp.add_argument("action", help="idea subcommand (use 'help' for full list)")
    sp.add_argument("args", nargs=argparse.REMAINDER, help="args passed to scripts/ops/idea.py (use '--' before flags)")
    sp.set_defaults(func=cmd_idea)

    sp = sub.add_parser("video", help="video/capcut helpers")
    sp.add_argument("--llm", choices=["api", "think", "codex"], default=_ops_default_llm_mode())
    sp.add_argument(
        "action",
        choices=[
            "factory",
            "auto-capcut",
            "bootstrap-run",
            "regen-images",
            "variants",
            "refresh-prompts",
            "audit-fix-drafts",
            "validate-prompts",
            "apply-source-mix",
        ],
        help="video operation",
    )
    sp.add_argument("args", nargs=argparse.REMAINDER, help="args passed to the underlying video tool (use '--' before flags)")
    sp.set_defaults(func=cmd_video)

    sp = sub.add_parser("thumbnails", help="thumbnail operations")
    sp.add_argument("--llm", choices=["api", "think", "codex"], default=_ops_default_llm_mode())
    sp.add_argument("action", choices=["build", "retake", "qc", "sync-inventory", "analyze", "help"])
    sp.add_argument("args", nargs=argparse.REMAINDER, help="args passed to the underlying thumbnail tool (use '--' before flags)")
    sp.set_defaults(func=cmd_thumbnails)

    sp = sub.add_parser("vision", help="vision pack (screenshot/thumbnail preprocess)")
    sp.add_argument("action", choices=["screenshot", "thumbnail", "help"])
    sp.add_argument("args", nargs=argparse.REMAINDER, help="args passed to scripts/vision/vision_pack.py (use '--' before flags)")
    sp.set_defaults(func=cmd_vision)

    sp = sub.add_parser("ui", help="start/stop/status UI stack")
    sp.add_argument("action", choices=["start", "stop", "status", "restart"])
    sp.set_defaults(func=cmd_ui)

    sp = sub.add_parser("agent", help="agent queue helper (agent_runner.py passthrough)")
    sp.add_argument("args", nargs=argparse.REMAINDER, help="args passed to scripts/agent_runner.py (use '--' before flags)")
    sp.set_defaults(func=cmd_agent)

    sp = sub.add_parser("slack", help="slack helpers (PM loop / inbox triage)")
    sp.add_argument("action", choices=["pm-loop"], help="slack operation")
    sp.add_argument("args", nargs=argparse.REMAINDER, help="args passed to scripts/ops/slack_pm_loop.py run (use '--' before flags)")
    sp.set_defaults(func=cmd_slack)

    sp = sub.add_parser("progress", help="derived episode progress view (read-only)")
    sp.add_argument("--channel", required=True, help="e.g. CH12")
    sp.add_argument("--videos", default="", help="Comma-separated (e.g. 012,013)")
    sp.add_argument("--format", choices=["tsv", "json", "summary", "table"], default="tsv")
    sp.add_argument("--issues-only", action="store_true")
    sp.add_argument("--include-unplanned", action="store_true")
    sp.add_argument("--include-hidden-runs", action="store_true")
    sp.set_defaults(func=cmd_progress)

    sp = sub.add_parser("reconcile", help="reconcile derived issues via fixed resume commands (dry-run by default)")
    sp.add_argument("--channel", required=True, help="e.g. CH12")
    sp.add_argument("--video", default="", help="Single video (e.g. 013)")
    sp.add_argument("--videos", default="", help="Comma-separated batch (e.g. 012,013)")
    sp.add_argument("--all", action="store_true", help="Allow --run without --video/--videos (dangerous)")
    sp.add_argument("--llm", choices=["api", "think", "codex"], default=_ops_default_llm_mode())
    sp.add_argument("--run", action="store_true", help="Execute planned resume actions (default: dry-run)")
    sp.add_argument("--skip-doctor", action="store_true", help="Skip ./ops doctor even when --run")
    sp.add_argument("--continue-on-error", action="store_true")
    sp.add_argument("--include-unplanned", action="store_true")
    sp.add_argument("--include-hidden-runs", action="store_true")
    sp.add_argument("--confirm-a", dest="confirm_a", action="store_true")
    sp.add_argument("--sync-mirror", dest="sync_mirror", action="store_true")
    sp.set_defaults(func=cmd_reconcile)

    sp = sub.add_parser("cleanup", help="cleanup helpers (safe by default)")
    sp.add_argument("action", choices=["workspace", "logs", "caches"], help="cleanup operation")
    sp.add_argument("args", nargs=argparse.REMAINDER, help="args passed to the underlying cleanup tool (use '--' before flags)")
    sp.set_defaults(func=cmd_cleanup)

    sp = sub.add_parser("snapshot", help="snapshot helpers (safe; read-only)")
    sp.add_argument("action", choices=["workspace", "logs"], help="snapshot operation")
    sp.add_argument("args", nargs=argparse.REMAINDER, help="args passed to the underlying snapshot tool (use '--' before flags)")
    sp.set_defaults(func=cmd_snapshot)

    sp = sub.add_parser("history", help="show recent ops runs (ops ledger)")
    sp.add_argument("--tail", type=int, default=30)
    sp.add_argument("--channel", help="filter by channel (e.g. CH12)")
    sp.add_argument("--video", help="filter by video (e.g. 013)")
    sp.add_argument("--only-cmd", dest="only_cmd", help="filter by top-level cmd (e.g. script/audio/video)")
    sp.add_argument("--failed-only", action="store_true", help="only show non-zero exit codes")
    sp.set_defaults(func=cmd_history)

    sp = sub.add_parser("patterns", help="execution patterns (SSOT recipes)")
    sp.add_argument("action", choices=["list", "show", "help"], nargs="?", default="help")
    sp.add_argument("pattern_id", nargs="?", help="pattern id (PAT-...) for `show`")
    sp.add_argument("--grep", default="", help="substring filter for `list` (id/title)")
    sp.add_argument("--json", action="store_true", help="emit JSON for `list`")
    sp.set_defaults(func=cmd_patterns)

    sp = sub.add_parser("latest", help="show latest ops runs (keep-latest pointers)")
    sp.add_argument("--only-cmd", dest="only_cmd", default="", help="filter by top-level cmd (writes cmd__<cmd>.json)")
    sp.add_argument("--channel", default="", help="filter by channel (e.g. CH12)")
    sp.add_argument("--video", default="", help="filter by video (e.g. 013)")
    sp.add_argument("--json", action="store_true", help="emit raw JSON")
    sp.set_defaults(func=cmd_latest)

    sp = sub.add_parser("inventory", help="generate/validate inventories")
    sp.add_argument("kind", choices=["scripts", "ssot", "docs"])
    sp.add_argument("--write", action="store_true", help="write report to file(s)")
    sp.add_argument("--check", action="store_true", help="fail if missing task defs (ssot only)")
    sp.set_defaults(func=cmd_inventory)

    sp = sub.add_parser("ssot", help="SSOT helpers (latest logic / audits)")
    sp.add_argument("action", choices=["status", "audit", "check"])
    sp.add_argument("args", nargs=argparse.REMAINDER, help="args passed to the underlying ssot tool (use '--' before flags)")
    sp.set_defaults(func=cmd_ssot)

    sp = sub.add_parser("episode", help="episode SSOT resolver (scripts/episode_ssot.py)")
    sp.add_argument(
        "action",
        choices=["show", "confirm-a", "auto-select-run", "set-run", "archive-runs", "materialize", "ensure", "help"],
        help="episode SSOT operation",
    )
    sp.add_argument("args", nargs=argparse.REMAINDER, help="args passed to scripts/episode_ssot.py (use '--' before flags)")
    sp.set_defaults(func=cmd_episode)

    sp = sub.add_parser("resume", help="fixed recovery commands (stable)")
    sp.add_argument("target", choices=["episode", "script", "audio", "video", "thumbnails"])
    sp.add_argument("args", nargs=argparse.REMAINDER, help="args passed to the underlying tool (use '--' before flags; include --llm here)")
    sp.set_defaults(func=cmd_resume)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _apply_ops_runtime_overrides(args)

    run_id = _new_run_id()
    llm = _llm_mode_from_args(args) or _llm_mode_from_argv(list(sys.argv[1:]))
    cmd_name = str(getattr(args, "cmd", "") or "").strip().lower()
    if llm is None and cmd_name in {"think", "api", "codex"}:
        llm = cmd_name
    if llm is None and cmd_name == "resume":
        # resume default follows ops default unless --llm is explicitly provided in forwarded args
        llm = _ops_default_llm_mode()
    llm = _apply_forced_llm(llm)
    exec_slot = _exec_slot_for_llm(llm)
    # ops-level exec-slot override (advanced) — do not apply to script pipeline (API-only policy).
    is_script_pipeline = cmd_name == "script" or (
        cmd_name == "resume" and str(getattr(args, "target", "") or "").strip().lower() == "script"
    )
    forced_exec_slot = _ops_force_exec_slot()
    if forced_exec_slot is not None and llm != "think" and not is_script_pipeline:
        exec_slot = forced_exec_slot
    op = _op_from_args(args)
    episode = _extract_episode_from_argv(list(sys.argv[1:]))
    git = _git_info()
    actor = _actor_info()
    started_at = _now_iso_utc()
    started = time.time()
    # Capture inner command logs only for high-value entrypoints (keeps noise down).
    cmd_name_norm = str(getattr(args, "cmd", "") or "").strip().lower() or None
    capture_logs = bool(cmd_name_norm and cmd_name_norm in _slack_notify_cmd_allowlist())
    _set_ops_run_context(run_id=run_id, cmd=cmd_name_norm, op=op, capture_logs=capture_logs)
    _append_ops_event(
        {
            "schema_version": 1,
            "kind": "ops_cli",
            "event": "start",
            "run_id": run_id,
            "at": started_at,
            "cmd": getattr(args, "cmd", None),
            "op": op,
            "llm": llm,
            "exec_slot": exec_slot,
            "exec_slot_override": forced_exec_slot,
            "episode": episode,
            "git": git,
            "actor": actor,
            "argv": sys.argv,
            "cwd": os.getcwd(),
        }
    )

    _maybe_print_ops_tips(args, llm=str(llm or ""), exec_slot=exec_slot, run_id=run_id)

    # Normalize "cmd -- <...>" expectation (avoid accidental empty command)
    if args.cmd == "cmd":
        # argparse includes leading '--' in REMAINDER on some shells; strip it.
        if args.command and args.command[0] == "--":
            args.command = args.command[1:]

    rc: int | None = None
    try:
        rc = int(args.func(args))
        return rc
    finally:
        elapsed_ms = int((time.time() - started) * 1000)
        pending = None
        if str(llm or "").strip().lower() == "think":
            pending = _pending_tasks_summary()
        finish_event = {
            "schema_version": 1,
            "kind": "ops_cli",
            "event": "finish",
            "run_id": run_id,
            "at": _now_iso_utc(),
            "started_at": started_at,
            "cmd": getattr(args, "cmd", None),
            "op": op,
            "llm": llm,
            "exec_slot": exec_slot,
            "exec_slot_override": forced_exec_slot,
            "episode": episode,
            "git": git,
            "actor": actor,
            "duration_ms": elapsed_ms,
            "exit_code": rc,
        }
        # Attach "next places to look" (Slack triage).
        try:
            finish_event["ops_latest"] = {"path": _fmt_relpath(_ops_latest_pointer_path_for_event(finish_event))}
        except Exception:
            pass
        if _OPS_CAPTURE_RUN_LOGS and _OPS_RUN_LOG_DIR is not None:
            try:
                finish_event["run_logs"] = {"dir": _fmt_relpath(_OPS_RUN_LOG_DIR)}
            except Exception:
                finish_event["run_logs"] = {"dir": str(_OPS_RUN_LOG_DIR)}
        if isinstance(_OPS_LAST_FAILED_RUN, dict):
            finish_event["failed_run"] = _OPS_LAST_FAILED_RUN
        if isinstance(_OPS_LAST_WARN_RUN, dict):
            finish_event["warn_run"] = _OPS_LAST_WARN_RUN
        if isinstance(_OPS_WARNINGS, dict):
            finish_event["warnings"] = _OPS_WARNINGS
        if _OPS_EXIT_CODE_RAW is not None:
            finish_event["exit_code_raw"] = _OPS_EXIT_CODE_RAW
        if isinstance(pending, dict):
            finish_event["pending"] = pending
        _append_ops_event(finish_event)
        _update_ops_latest_pointers(finish_event)
        _maybe_slack_notify(finish_event)


if __name__ == "__main__":
    raise SystemExit(main())

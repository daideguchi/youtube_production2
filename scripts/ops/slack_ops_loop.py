#!/usr/bin/env python3
from __future__ import annotations

"""
slack_ops_loop.py — Slackスレッドからローカル `./ops` を安全に実行する（ローカル専用）

目的:
- スマホ/外出先から、Slackスレへ1行コマンドを投げて、このMac上で `./ops ...` を実行する。
- 結果は同じスレへ返信する（短く要約。詳細はローカルログへ）。

安全:
- 任意コマンドは禁止。allowlist のみ対応。
- botメッセージは無視。実行ユーザーは dd に限定する運用を推奨。
- Slack識別子は git に保存しない（state/log は workspaces/logs 配下のみ）。

SSOT:
- ssot/ops/OPS_SLACK_OPS_GATEWAY.md
"""

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from _bootstrap import bootstrap

bootstrap(load_env=True)

from factory_common.paths import logs_root, repo_root  # noqa: E402

ROOT = repo_root()
LOGS = logs_root()

_ACK_WORDS = {
    "ok",
    "okay",
    "thanks",
    "thx",
    "了解",
    "了解です",
    "りょ",
    "りょうかい",
    "はい",
    "うん",
    "ありがとう",
    "サンクス",
}

_CHANNEL_RE = re.compile(r"^CH\d{2}$", re.IGNORECASE)
_VIDEO_RE = re.compile(r"^\d{3}$")
_MENTION_RE = re.compile(r"<@U[A-Z0-9]+>")


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _thread_key(channel: str, thread_ts: str) -> str:
    raw = f"{str(channel).strip()}|{str(thread_ts).strip()}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:10]


def _base_dir(channel: str, thread_ts: str) -> Path:
    return LOGS / "ops" / "slack_ops_loop" / _thread_key(channel, thread_ts)


def _lock_path(channel: str, thread_ts: str) -> Path:
    return _base_dir(channel, thread_ts) / "lock"


def _state_path(channel: str, thread_ts: str) -> Path:
    return _base_dir(channel, thread_ts) / "state.json"


def _runs_dir(channel: str, thread_ts: str) -> Path:
    return _base_dir(channel, thread_ts) / "runs"


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _load_state(path: Path) -> Dict[str, Any]:
    try:
        if not path.exists():
            return {"schema_version": 1, "cursor_ts": "0", "updated_at": _now_iso_utc()}
        obj = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(obj, dict):
            return {"schema_version": 1, "cursor_ts": "0", "updated_at": _now_iso_utc()}
        if "cursor_ts" not in obj:
            obj["cursor_ts"] = "0"
        if "schema_version" not in obj:
            obj["schema_version"] = 1
        return obj
    except Exception:
        return {"schema_version": 1, "cursor_ts": "0", "updated_at": _now_iso_utc()}


def _save_state(path: Path, state: Dict[str, Any]) -> None:
    try:
        _ensure_dir(path.parent)
        payload = dict(state or {})
        payload["updated_at"] = _now_iso_utc()
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
    except Exception:
        return


def _cursor_ts_float(state: Dict[str, Any]) -> float:
    raw = str((state or {}).get("cursor_ts") or "").strip()
    try:
        return float(raw) if raw else 0.0
    except Exception:
        return 0.0


def _set_cursor_ts(state: Dict[str, Any], ts: float) -> None:
    try:
        state["cursor_ts"] = str(float(ts))
    except Exception:
        state["cursor_ts"] = str(ts)


def _bot_token_present() -> bool:
    return bool(str(os.getenv("SLACK_BOT_TOKEN") or os.getenv("YTM_SLACK_BOT_TOKEN") or "").strip())


def _slack_notify_path() -> Path:
    return ROOT / "scripts" / "ops" / "slack_notify.py"


def _run_slack_notify_json(args: List[str], *, out_json: Path) -> Dict[str, Any]:
    cmd = [sys.executable, str(_slack_notify_path()), *args]
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"slack_notify failed (exit={proc.returncode}): {err[:400]}")
    if not out_json.exists():
        # slack_notify may return 0 even when poll fails (best-effort).
        return {}
    try:
        return json.loads(out_json.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _poll_thread_replies(
    *,
    channel: str,
    thread_ts: str,
    oldest: str,
    limit: int,
    out_json: Path,
) -> List[Dict[str, Any]]:
    args = [
        "--channel",
        str(channel),
        "--poll-thread",
        str(thread_ts),
        "--poll-limit",
        str(max(1, int(limit))),
        "--poll-out-json",
        str(out_json),
    ]
    if str(oldest or "").strip() and str(oldest).strip() not in {"0", "0.0"}:
        args += ["--poll-oldest", str(oldest).strip()]
    obj = _run_slack_notify_json(args, out_json=out_json)
    replies = obj.get("replies") if isinstance(obj, dict) else None
    return replies if isinstance(replies, list) else []


def _post_thread_reply(*, channel: str, thread_ts: str, text: str, dry_run: bool) -> None:
    msg = str(text or "").strip()
    if not msg:
        return
    if dry_run:
        print("[dry-run] slack reply:", msg)
        return
    out_json = _base_dir(channel, thread_ts) / "last_send.json"
    args = [
        "--channel",
        str(channel),
        "--thread-ts",
        str(thread_ts),
        "--text",
        msg,
        "--out-json",
        str(out_json),
    ]
    _run_slack_notify_json(args, out_json=out_json)


def _is_bot_message(msg: Dict[str, Any]) -> bool:
    subtype = str(msg.get("subtype") or "").strip()
    if subtype in {"bot_message", "channel_join", "channel_leave"}:
        return True
    if msg.get("bot_id"):
        return True
    return False


def _ts_float(msg: Dict[str, Any]) -> float:
    try:
        return float(str(msg.get("ts") or "").strip())
    except Exception:
        return 0.0


def _short(s: str, *, max_len: int) -> str:
    raw = str(s or "").strip()
    if len(raw) <= max_len:
        return raw
    return raw[: max(0, max_len - 1)] + "…"


def _truncate_block(text: str, *, max_chars: int = 2400, max_lines: int = 80) -> str:
    s = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = s.splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines] + ["…(truncated)"]
    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[: max(0, max_chars - 1)] + "…"
    return out


def _normalize_text(raw: str) -> str:
    s = str(raw or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    s = _MENTION_RE.sub("", s).strip()
    if s.lower().startswith("ytm"):
        # "ytm ..." / "ytm: ..." / "ytm　..."
        s = s[3:].lstrip(" :\u3000").strip()
    return s


def _had_ytm_prefix(raw: str) -> bool:
    s = str(raw or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    s = _MENTION_RE.sub("", s).lstrip()
    if not s.lower().startswith("ytm"):
        return False
    if len(s) == 3:
        return True
    return bool(s[3] in {" ", ":", "\t", "\u3000", "\n"})


def _looks_ack(text: str) -> bool:
    compact = re.sub(r"[\s\u3000]+", "", str(text or "")).strip().lower()
    return bool(compact in {re.sub(r"[\s\u3000]+", "", w).lower() for w in _ACK_WORDS})


def _parse_channel(token: str) -> Optional[str]:
    s = str(token or "").strip().upper()
    if _CHANNEL_RE.match(s):
        return s
    return None


def _parse_video(token: str) -> Optional[str]:
    s = str(token or "").strip()
    if _VIDEO_RE.match(s):
        return s
    return None


@dataclass(frozen=True)
class ParsedCommand:
    kind: str
    display: str
    ops_argv: Optional[List[str]] = None
    reply_only_text: Optional[str] = None


def _help_text() -> str:
    return "\n".join(
        [
            "使い方（例）:",
            "- help",
            "- ui status | ui restart",
            "- latest",
            "- progress CH01",
            "- idea add CH01 <working-title>",
            "- script rewrite CH01 019 <instruction>",
            "",
            "メモ:",
            "- コマンドは誤爆防止のため `ytm ...` プレフィックス必須（例: `ytm ui status`）。",
            "- 実行は allowlist のみ。曖昧な依頼は追加情報を促します。",
        ]
    ).strip()


def _parse_command(text: str) -> ParsedCommand:
    t = _normalize_text(text)
    if not t:
        return ParsedCommand(kind="ignore", display="(empty)")
    if _looks_ack(t):
        return ParsedCommand(kind="ignore", display="ack")

    parts = [p for p in t.split() if p.strip()]
    head = parts[0].lower() if parts else ""

    if head in {"help", "h", "?"}:
        return ParsedCommand(kind="reply_only", display="help", reply_only_text=_help_text())

    if head == "ui" and len(parts) >= 2:
        action = parts[1].lower()
        if action in {"status", "start", "stop", "restart"}:
            return ParsedCommand(kind="ops", display=f"ui {action}", ops_argv=["ui", action])

    if head == "latest":
        return ParsedCommand(kind="ops", display="latest", ops_argv=["latest"])

    if head == "progress" and len(parts) >= 2:
        ch = _parse_channel(parts[1])
        if not ch:
            return ParsedCommand(kind="reply_only", display="progress", reply_only_text="progress: `progress CH01` の形式で指定して下さい。")
        return ParsedCommand(kind="ops", display=f"progress {ch}", ops_argv=["progress", "--channel", ch, "--format", "summary"])

    if head == "idea" and len(parts) >= 4 and parts[1].lower() == "add":
        ch = _parse_channel(parts[2])
        if not ch:
            return ParsedCommand(kind="reply_only", display="idea add", reply_only_text="idea add: `idea add CH01 <working-title>` の形式で指定して下さい。")
        title = " ".join(parts[3:]).strip()
        if not title:
            return ParsedCommand(kind="reply_only", display="idea add", reply_only_text="idea add: working-title が空です。")
        return ParsedCommand(
            kind="ops",
            display=f"idea add {ch}",
            ops_argv=["idea", "add", "--", "--channel", ch, "--working-title", title],
        )

    if head == "script" and len(parts) >= 2:
        action = parts[1].lower()
        if action in {"resume", "rewrite"}:
            if len(parts) < 4:
                return ParsedCommand(kind="reply_only", display=f"script {action}", reply_only_text=f"script {action}: `script {action} CH01 019 ...` の形式で指定して下さい。")
            ch = _parse_channel(parts[2])
            vid = _parse_video(parts[3])
            if not (ch and vid):
                return ParsedCommand(kind="reply_only", display=f"script {action}", reply_only_text=f"script {action}: `script {action} CH01 019 ...` の形式で指定して下さい。")
            if action == "resume":
                return ParsedCommand(
                    kind="ops",
                    display=f"script resume {ch} {vid}",
                    ops_argv=["api", "script", "resume", "--", "--channel", ch, "--video", vid],
                )
            instruction = " ".join(parts[4:]).strip()
            if not instruction:
                return ParsedCommand(
                    kind="reply_only",
                    display=f"script rewrite {ch} {vid}",
                    reply_only_text="script rewrite: 指示文が必要です。例: `script rewrite CH01 019 言い回しをもっと分かりやすく`",
                )
            return ParsedCommand(
                kind="ops",
                display=f"script rewrite {ch} {vid}",
                ops_argv=["api", "script", "rewrite", "--", "--channel", ch, "--video", vid, "--instruction", instruction],
            )

        if action == "redo-full":
            if len(parts) < 4:
                return ParsedCommand(kind="reply_only", display="script redo-full", reply_only_text="script redo-full: `script redo-full CH01 019` の形式で指定して下さい。")
            ch = _parse_channel(parts[2])
            vid = _parse_video(parts[3])
            if not (ch and vid):
                return ParsedCommand(kind="reply_only", display="script redo-full", reply_only_text="script redo-full: `script redo-full CH01 019` の形式で指定して下さい。")
            return ParsedCommand(
                kind="ops",
                display=f"script redo-full {ch} {vid}",
                ops_argv=["api", "script", "redo-full", "--", "--channel", ch, "--from", vid, "--to", vid],
            )

    return ParsedCommand(
        kind="unknown",
        display=_short(t, max_len=60),
        reply_only_text="未対応コマンドです。`help` を見て下さい。",
    )


@dataclass(frozen=True)
class RunResult:
    argv: List[str]
    exit_code: int
    output: str
    log_path: Optional[Path]


def _run_ops(ops_argv: List[str], *, channel: str, thread_ts: str, dry_run: bool) -> RunResult:
    cmd = [str(ROOT / "ops"), *[str(x) for x in (ops_argv or [])]]
    run_dir = _runs_dir(channel, thread_ts)
    _ensure_dir(run_dir)
    stem = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = hashlib.sha256((" ".join(cmd)).encode("utf-8")).hexdigest()[:10]
    log_path = run_dir / f"run__{stem}__{suffix}.log"

    if dry_run:
        payload = "\n".join(["# dry-run", "# argv: " + " ".join(cmd), ""])
        log_path.write_text(payload, encoding="utf-8")
        return RunResult(argv=cmd, exit_code=0, output=payload, log_path=log_path)

    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, check=False)
    out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    output = out.strip()
    header = "\n".join(
        [
            "# slack_ops_loop",
            f"# at: {_now_iso_utc()}",
            "# argv: " + " ".join(cmd),
            f"# exit_code: {int(proc.returncode)}",
            "",
        ]
    )
    try:
        log_path.write_text(header + (output + "\n" if output else ""), encoding="utf-8", errors="replace")
    except Exception:
        log_path = None
    return RunResult(argv=cmd, exit_code=int(proc.returncode), output=output, log_path=log_path)


def _with_single_instance_lock(lock_path: Path) -> Tuple[Optional[Any], Optional[str]]:
    _ensure_dir(lock_path.parent)
    f = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        try:
            f.close()
        except Exception:
            pass
        return None, "already running"
    try:
        f.seek(0)
        f.truncate()
        f.write(f"pid={os.getpid()} started_at={_now_iso_utc()}\n")
        f.flush()
    except Exception:
        pass
    return f, None


def _run_once(args: argparse.Namespace) -> int:
    channel = str(args.channel or "").strip()
    thread_ts = str(args.thread_ts or "").strip()
    if not channel or not thread_ts:
        raise SystemExit("missing --channel / --thread-ts")
    if not _bot_token_present():
        raise SystemExit("missing SLACK_BOT_TOKEN (or YTM_SLACK_BOT_TOKEN). See ssot/ops/OPS_ENV_VARS.md")

    lock_handle, lock_err = _with_single_instance_lock(_lock_path(channel, thread_ts))
    if not lock_handle:
        if lock_err:
            print(f"[slack_ops_loop] {lock_err}")
        return 0

    dry_run = bool(getattr(args, "dry_run", False))
    allowed_users = {str(x).strip() for x in (args.allow_user or []) if str(x).strip()}
    dd_user = str(getattr(args, "dd_user", "") or "").strip()
    if dd_user:
        allowed_users.add(dd_user)

    state_p = _state_path(channel, thread_ts)
    state = _load_state(state_p)
    cursor = _cursor_ts_float(state)
    cursor_raw = str(state.get("cursor_ts") or "").strip() or "0"

    poll_json = _base_dir(channel, thread_ts) / "last_poll.json"
    replies = _poll_thread_replies(
        channel=channel,
        thread_ts=thread_ts,
        oldest=cursor_raw,
        limit=int(getattr(args, "poll_limit", 200) or 200),
        out_json=poll_json,
    )
    if not replies:
        return 0

    # Process in chronological order.
    replies_sorted = sorted([r for r in replies if isinstance(r, dict)], key=_ts_float)

    max_cmds = int(getattr(args, "max_commands", 3) or 3)
    handled = 0
    cursor_next = cursor
    require_prefix = True

    for msg in replies_sorted:
        ts = _ts_float(msg)
        if ts <= cursor:
            cursor_next = max(cursor_next, ts)
            continue

        if handled >= max_cmds:
            # Leave cursor at the last handled message so remaining commands are processed next run.
            break

        if _is_bot_message(msg):
            cursor_next = max(cursor_next, ts)
            continue

        user = str(msg.get("user") or "").strip()
        if allowed_users and user and (user not in allowed_users):
            cursor_next = max(cursor_next, ts)
            continue

        text = str(msg.get("text") or "")
        if require_prefix and not _had_ytm_prefix(text):
            cursor_next = max(cursor_next, ts)
            continue
        parsed = _parse_command(text)
        if parsed.kind == "ignore":
            cursor_next = max(cursor_next, ts)
            continue

        handled += 1

        if parsed.kind == "reply_only":
            _post_thread_reply(
                channel=channel,
                thread_ts=thread_ts,
                text=str(parsed.reply_only_text or "").strip(),
                dry_run=dry_run,
            )
            cursor_next = max(cursor_next, ts)
            continue

        if parsed.kind == "unknown":
            _post_thread_reply(channel=channel, thread_ts=thread_ts, text=str(parsed.reply_only_text or ""), dry_run=dry_run)
            cursor_next = max(cursor_next, ts)
            continue

        if parsed.kind == "ops":
            _post_thread_reply(channel=channel, thread_ts=thread_ts, text=f"run: `{parsed.display}`", dry_run=dry_run)
            res = _run_ops(list(parsed.ops_argv or []), channel=channel, thread_ts=thread_ts, dry_run=dry_run)
            status = "OK" if res.exit_code == 0 else f"FAILED(exit={res.exit_code})"
            out = _truncate_block(res.output, max_chars=int(getattr(args, "max_reply_chars", 2400) or 2400))
            body_lines = [f"{status}: `{parsed.display}`"]
            if out.strip():
                body_lines.append("```")
                body_lines.append(out)
                body_lines.append("```")
            if res.log_path:
                try:
                    rel = str(res.log_path.resolve().relative_to(ROOT))
                except Exception:
                    rel = str(res.log_path)
                body_lines.append(f"log: `{rel}`")
            _post_thread_reply(channel=channel, thread_ts=thread_ts, text="\n".join(body_lines).strip(), dry_run=dry_run)
            cursor_next = max(cursor_next, ts)
            continue

    _set_cursor_ts(state, cursor_next)
    _save_state(state_p, state)
    try:
        lock_handle.close()
    except Exception:
        pass
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Slack thread -> local ./ops gateway (local only).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("run", help="Poll thread replies once and execute allowlisted commands.")
    sp.add_argument("--channel", required=True, help="Slack channel id/name (not stored in git).")
    sp.add_argument("--thread-ts", required=True, help="Slack thread ts (parent message ts).")
    sp.add_argument("--dd-user", default="", help="Allowed Slack user id (recommended).")
    sp.add_argument("--allow-user", action="append", default=[], help="Additional allowed Slack user ids (repeatable).")
    sp.add_argument("--poll-limit", type=int, default=200, help="Max thread replies to fetch per run (default: 200).")
    sp.add_argument("--max-commands", type=int, default=3, help="Max commands to execute per run (default: 3).")
    sp.add_argument("--max-reply-chars", type=int, default=2400, help="Max chars to include from stdout/stderr in Slack reply.")
    sp.add_argument("--dry-run", action="store_true", help="Do not execute ./ops; only echo planned actions.")
    sp.set_defaults(func=_run_once)

    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

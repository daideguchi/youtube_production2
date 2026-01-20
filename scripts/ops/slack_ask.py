from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from factory_common.paths import repo_root, workspace_root


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _ops_logs_dir() -> Path:
    return workspace_root() / "logs" / "ops"


def _ask_store_dir() -> Path:
    return _ops_logs_dir() / "slack_asks"


def _ask_map_path() -> Path:
    return _ask_store_dir() / "ask_map.json"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, obj: Any) -> None:
    _ensure_dir(path.parent)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_map() -> dict[str, Any]:
    p = _ask_map_path()
    if not p.exists():
        return {"schema": 1, "updated_at": _now_iso_utc(), "asks": {}}
    try:
        obj = _load_json(p)
        if isinstance(obj, dict) and isinstance(obj.get("asks"), dict):
            return obj
    except Exception:
        pass
    return {"schema": 1, "updated_at": _now_iso_utc(), "asks": {}}


def _save_map(obj: dict[str, Any]) -> None:
    obj["updated_at"] = _now_iso_utc()
    _write_json(_ask_map_path(), obj)


def _force_bot_mode_env() -> dict[str, str]:
    env = dict(os.environ)
    env["YTM_SLACK_WEBHOOK_URL"] = ""
    env["SLACK_WEBHOOK_URL"] = ""
    return env


@dataclass(frozen=True)
class AskRecord:
    ask_id: str
    channel: str
    thread_ts: str
    created_at: str
    subject: str
    send_json: str


def _make_ask_id(raw: Optional[str]) -> str:
    s = str(raw or "").strip()
    if s:
        return s
    return f"ask__{_now_compact()}"


def _build_text(ask_id: str, subject: str, body: str) -> str:
    lines = []
    lines.append(f"*【ASK】 {subject}*")
    lines.append(f"ask_id: `{ask_id}`")
    lines.append("")
    lines.append("返信ルール（固定）:")
    lines.append("- このメッセージの *thread* に返信してください（推奨）")
    lines.append(f"- もしチャンネルに返す場合も、本文に `ask_id: {ask_id}` を含めてください")
    if body:
        lines.append("")
        lines.append(body)
    return "\n".join(lines).strip() + "\n"


def _run_slack_notify(args: list[str], *, channel: str) -> int:
    cmd = [sys.executable, str(repo_root() / "scripts" / "ops" / "slack_notify.py"), *args]
    if channel:
        cmd += ["--channel", channel]
    proc = subprocess.run(cmd, env=_force_bot_mode_env(), text=True)
    return int(proc.returncode)


def _send_ask(*, ask_id: str, subject: str, body: str, channel: str) -> AskRecord:
    _ensure_dir(_ask_store_dir())
    send_json = _ask_store_dir() / f"{ask_id}__send.json"
    text = _build_text(ask_id=ask_id, subject=subject, body=body)

    rc = _run_slack_notify(["--text", text, "--out-json", str(send_json), "--print-ts"], channel=channel)
    if rc != 0:
        raise SystemExit(rc)

    resp = _load_json(send_json)
    ts = ""
    if isinstance(resp, dict):
        ts = str(resp.get("ts") or "").strip()
        if not ts and isinstance(resp.get("message"), dict):
            ts = str(resp["message"].get("ts") or "").strip()
    if not ts:
        raise SystemExit("[slack_ask] could not determine thread ts from Slack response")

    rec = AskRecord(
        ask_id=ask_id,
        channel=channel,
        thread_ts=ts,
        created_at=_now_iso_utc(),
        subject=subject,
        send_json=str(send_json),
    )
    m = _load_map()
    m["asks"][ask_id] = {
        "ask_id": ask_id,
        "created_at": rec.created_at,
        "channel": channel,
        "thread_ts": ts,
        "subject": subject,
        "send_json": str(send_json),
    }
    _save_map(m)
    return rec


def _poll_thread(*, ask_id: str, thread_ts: str, channel: str, oldest: str, write_memos: bool) -> Path:
    _ensure_dir(_ask_store_dir())
    out_json = _ask_store_dir() / f"{ask_id}__poll.json"
    args = ["--poll-thread", thread_ts, "--poll-oldest", oldest, "--poll-out-json", str(out_json)]
    if write_memos:
        args.append("--poll-write-memos")
    rc = _run_slack_notify(args, channel=channel)
    if rc != 0:
        raise SystemExit(rc)
    return out_json


def _poll_until_reply(
    *,
    ask_id: str,
    thread_ts: str,
    channel: str,
    wait_sec: int,
    interval_sec: int,
    write_memos: bool,
) -> Path:
    start = time.time()
    oldest = thread_ts
    last_out: Optional[Path] = None
    while True:
        last_out = _poll_thread(
            ask_id=ask_id,
            thread_ts=thread_ts,
            channel=channel,
            oldest=oldest,
            write_memos=write_memos,
        )
        obj = _load_json(last_out)
        reply_count = int(obj.get("reply_count") or 0) if isinstance(obj, dict) else 0
        if reply_count > 0:
            return last_out
        if wait_sec <= 0:
            return last_out
        if (time.time() - start) >= wait_sec:
            return last_out
        time.sleep(max(1, int(interval_sec or 10)))


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Ask on Slack and persist thread ts for reliable polling.")
    sp = ap.add_subparsers(dest="cmd", required=True)

    ap_ask = sp.add_parser("ask", help="Send a question and optionally wait for replies.")
    ap_ask.add_argument("--subject", required=True)
    ap_ask.add_argument("--body", default="")
    ap_ask.add_argument("--ask-id", default="")
    ap_ask.add_argument("--channel", default="")
    ap_ask.add_argument("--wait-sec", type=int, default=0)
    ap_ask.add_argument("--poll-interval-sec", type=int, default=15)
    ap_ask.add_argument("--write-memos", action="store_true")

    ap_poll = sp.add_parser("poll", help="Poll replies for an existing ask_id (uses local ask_map.json).")
    ap_poll.add_argument("--ask-id", required=True)
    ap_poll.add_argument("--channel", default="")
    ap_poll.add_argument("--write-memos", action="store_true")

    args = ap.parse_args(argv)
    cmd = str(args.cmd or "").strip()

    if cmd == "ask":
        ask_id = _make_ask_id(args.ask_id)
        rec = _send_ask(
            ask_id=ask_id,
            subject=str(args.subject or "").strip(),
            body=str(args.body or "").strip(),
            channel=str(args.channel or "").strip(),
        )
        out = _poll_until_reply(
            ask_id=rec.ask_id,
            thread_ts=rec.thread_ts,
            channel=rec.channel,
            wait_sec=int(args.wait_sec or 0),
            interval_sec=int(args.poll_interval_sec or 15),
            write_memos=bool(args.write_memos),
        )
        print(json.dumps({"ask_id": rec.ask_id, "thread_ts": rec.thread_ts, "poll_json": str(out)}, ensure_ascii=False))
        return 0

    if cmd == "poll":
        ask_id = str(args.ask_id or "").strip()
        m = _load_map()
        asks = m.get("asks") if isinstance(m, dict) else None
        ent = asks.get(ask_id) if isinstance(asks, dict) else None
        if not isinstance(ent, dict):
            print(f"[slack_ask] unknown ask_id: {ask_id}", file=sys.stderr)
            return 2
        thread_ts = str(ent.get("thread_ts") or "").strip()
        channel = str(args.channel or "").strip() or str(ent.get("channel") or "").strip()
        out = _poll_until_reply(
            ask_id=ask_id,
            thread_ts=thread_ts,
            channel=channel,
            wait_sec=0,
            interval_sec=15,
            write_memos=bool(args.write_memos),
        )
        print(json.dumps({"ask_id": ask_id, "thread_ts": thread_ts, "poll_json": str(out)}, ensure_ascii=False))
        return 0

    print(f"unknown cmd: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())


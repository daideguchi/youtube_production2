#!/usr/bin/env python3
from __future__ import annotations

"""
slack_inbox_sync.py — Slack→Git “PM Inbox” synchronizer (digest only)

Goal:
- Do NOT store Slack identifiers (channel_id/user_id/thread_ts) in git.
- Do store a small, redacted, hash-keyed digest in:
  - ssot/history/HISTORY_slack_pm_inbox.md
- Keep raw mapping (hash_key -> slack ids) under workspaces/logs/ops/ (NOT tracked).

SSOT:
- ssot/plans/PLAN_OPS_SLACK_GIT_ARCHIVE.md
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from _bootstrap import bootstrap

bootstrap(load_env=True)

from factory_common.paths import logs_root, repo_root  # noqa: E402

ROOT = repo_root()

_INBOX_START = "<!-- inbox:start -->"
_INBOX_END = "<!-- inbox:end -->"

_SUSPECT_SECRET_TOKEN_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bfw_[A-Za-z0-9]{8,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{10,}\b"),
    re.compile(r"\bya29\.[A-Za-z0-9_-]{20,}\b"),
]
_ENV_ASSIGNMENT_RE = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*)$")
_SUSPECT_SECRET_NAME_HINTS = (
    "TOKEN",
    "API_KEY",
    "SECRET",
    "PASSWORD",
    "PRIVATE_KEY",
    "ACCESS_KEY",
)


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _redact_text(text: str) -> tuple[str, bool]:
    raw = str(text or "")
    out = raw.replace("\r\n", "\n").replace("\r", "\n")
    redacted = False
    for pat in _SUSPECT_SECRET_TOKEN_PATTERNS:
        if pat.search(out):
            redacted = True
        out = pat.sub("[REDACTED]", out)

    lines: list[str] = []
    for ln in out.splitlines():
        m = _ENV_ASSIGNMENT_RE.match(ln.strip())
        if not m:
            lines.append(ln)
            continue
        name = str(m.group("name") or "").strip()
        val = str(m.group("value") or "").strip()
        upper = name.upper()
        if name and val and any(h in upper for h in _SUSPECT_SECRET_NAME_HINTS):
            redacted = True
            lines.append(f"{name}=[REDACTED]")
        else:
            lines.append(ln)
    return ("\n".join(lines)).strip(), redacted


def _first_line(text: str, *, max_len: int = 160) -> str:
    s = str(text or "").strip().replace("\r\n", "\n").replace("\r", "\n")
    first = s.split("\n", 1)[0].strip()
    if len(first) <= max_len:
        return first
    return first[: max_len - 1] + "…"


def _ts_to_iso(ts: str) -> str:
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return _now_iso_utc()


def _hash_key(*parts: str) -> str:
    payload = "|".join([str(p or "") for p in parts]).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:10]


def _slack_notify_path() -> Path:
    return ROOT / "scripts" / "ops" / "slack_notify.py"


def _run_slack_notify_json(args: list[str], *, out_json: Path) -> Dict[str, Any]:
    cmd = [sys.executable, str(_slack_notify_path()), *args, "--poll-out-json", str(out_json)]
    # NOTE: slack_notify handles token/channel lookup via env; we do not print secrets.
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"slack_notify failed (exit={proc.returncode}): {err[:400]}")
    try:
        return json.loads(out_json.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"failed to parse slack_notify output: {out_json} ({exc})") from exc


def _run_slack_history_json(args: list[str], *, out_json: Path) -> Dict[str, Any]:
    cmd = [sys.executable, str(_slack_notify_path()), *args, "--history-out-json", str(out_json)]
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"slack_notify history failed (exit={proc.returncode}): {err[:400]}")
    try:
        return json.loads(out_json.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"failed to parse slack_notify output: {out_json} ({exc})") from exc


def _is_bot_message(msg: Dict[str, Any]) -> bool:
    subtype = str(msg.get("subtype") or "").strip()
    if subtype in {"bot_message", "channel_join", "channel_leave"}:
        return True
    if msg.get("bot_id"):
        return True
    return False


@dataclass(frozen=True)
class InboxItem:
    key: str
    when_iso: str
    who: str
    source: str  # "thread" | "channel"
    kind: str  # decision|rule|question|request|ack|thanks|note
    redacted: bool
    text: str

    def to_md_line(self, *, checked: bool) -> str:
        red = "redacted" if self.redacted else "plain"
        # Keep single-line & compact for mobile reading.
        mark = "x" if checked else " "
        return f"- [{mark}] {self.when_iso} key={self.key} src={self.source} kind={self.kind} who={self.who} {red} | {self.text}"


def _compact_text(s: str) -> str:
    return re.sub(r"[\s\u3000]+", "", str(s or "")).strip().lower()


def _classify_message(text: str) -> str:
    """
    Heuristic classifier for PM signal extraction.
    (No LLM usage; deterministic.)
    """
    raw = str(text or "").strip()
    if not raw:
        return "note"

    # Strip slack emoji tokens for classification (":ok_hand:")
    stripped = re.sub(r":[a-z0-9_+-]+:", "", raw, flags=re.IGNORECASE)
    compact = _compact_text(stripped)

    # Acknowledge-only noise.
    if compact in {"了解", "ok", "okay", "okhand", "ok_hand", "了解ok", "了解okhand", "了解ok_hand"}:
        return "ack"

    # Questions.
    if "?" in raw or "？" in raw:
        return "question"

    # Thanks / praise (non-actionable). Keep this before "request" heuristics
    # (e.g. "作ってくれてありがとう" contains "作って").
    if any(k in raw for k in ["ありがとう", "嬉しい", "助かる"]):
        return "thanks"

    low = raw.lower()
    if "llm smoke" in low or "smoke" in low:
        return "request"

    # Decisions (short but meaningful).
    if re.search(r"(^|[\\s\\u3000])[a-z]はok(\\b|$)", raw, flags=re.IGNORECASE) or ("はok" in low) or ("はｏｋ" in _compact_text(raw)):
        return "decision"

    # Rules / prohibitions.
    if any(k in raw for k in ["禁止", "しない", "やめ", "廃止", "抹消", "固定", "絶対"]):
        return "rule"

    # Requests / action commands.
    if any(k in raw for k in ["進め", "やって", "実装", "確認", "送って", "整理", "作って", "対応", "修正", "更新"]):
        return "request"

    return "note"


def _actionable(kind: str) -> bool:
    return kind in {"decision", "rule", "question", "request"}


def _load_inbox_md(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmpl = "\n".join(
        [
            "# HISTORY_slack_pm_inbox — PM Inbox（Slack要約; Git書庫）",
            "",
            "目的:",
            "- Slackに埋もれる dd の指示/決定/質問を、**取りこぼさず**に追える形にする。",
            "- ただし、Slackの生ログや識別子（channel_id/user_id/thread_ts 等）は **gitに固定しない**。",
            "",
            "更新:",
            "- 生成/追記: `python3 scripts/ops/slack_inbox_sync.py sync --write-ssot`",
            "- 運用正本: `ssot/plans/PLAN_OPS_SLACK_GIT_ARCHIVE.md`",
            "",
            "注意（安全）:",
            "- 本文は短く切り、token-like文字列は `[REDACTED]` に置換される。",
            "- Slack側の一次情報（全文/文脈）はSlackで確認する（このファイルは“PM用の要約Inbox”）。",
            "",
            "---",
            "",
            "## Inbox（auto）",
            _INBOX_START,
            _INBOX_END,
            "",
        ]
    )
    path.write_text(tmpl + "\n", encoding="utf-8")
    return tmpl + "\n"


def _extract_existing_keys(md: str) -> set[str]:
    keys: set[str] = set()
    in_box = False
    for ln in md.splitlines():
        if ln.strip() == _INBOX_START:
            in_box = True
            continue
        if ln.strip() == _INBOX_END:
            break
        if not in_box:
            continue
        m = re.search(r"\bkey=([0-9a-f]{10})\b", ln)
        if m:
            keys.add(m.group(1))
    return keys


def _extract_existing_checks(md: str) -> Dict[str, bool]:
    """
    Returns key -> checked (True if [x]).
    """
    out: Dict[str, bool] = {}
    in_box = False
    for ln in md.splitlines():
        if ln.strip() == _INBOX_START:
            in_box = True
            continue
        if ln.strip() == _INBOX_END:
            break
        if not in_box:
            continue
        m = re.search(r"\bkey=([0-9a-f]{10})\b", ln)
        if not m:
            continue
        key = m.group(1)
        s = ln.strip()
        checked = s.startswith("- [x]") or s.startswith("- [X]")
        out[key] = checked
    return out


def _replace_inbox_block(md: str, new_lines: list[str]) -> str:
    lines = md.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == _INBOX_START)
        end = next(i for i, ln in enumerate(lines) if ln.strip() == _INBOX_END)
    except StopIteration:
        raise RuntimeError("inbox markers not found in markdown; expected inbox:start/end")
    if end < start:
        raise RuntimeError("invalid inbox markers order")
    out = []
    out.extend(lines[: start + 1])
    out.extend(new_lines)
    out.extend(lines[end:])
    return "\n".join(out) + "\n"


def _default_out_md() -> Path:
    return ROOT / "ssot" / "history" / "HISTORY_slack_pm_inbox.md"


def _default_map_path() -> Path:
    out_dir = logs_root() / "ops"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / "slack_pm_inbox_map.json"


def _save_map(path: Path, mapping: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_map(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "updated_at": _now_iso_utc(), "items": {}}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(obj, dict) and isinstance(obj.get("items"), dict):
            return obj
    except Exception:
        pass
    return {"schema_version": 1, "updated_at": _now_iso_utc(), "items": {}}


def _post_digest_to_slack(*, channel: str, thread_ts: str, items: list[InboxItem], out_md: Path, limit: int) -> str:
    """
    Post a short digest back into the same Slack thread.
    (Slack IDs are never written to git; this is just a convenience reply.)
    """
    if not channel or not thread_ts:
        raise ValueError("missing channel/thread_ts")

    safe_n = max(0, int(limit))
    picked = items[:safe_n] if safe_n else items

    rel = None
    try:
        rel = str(out_md.resolve().relative_to(ROOT))
    except Exception:
        rel = str(out_md)

    lines: list[str] = []
    lines.append("*【PM Inbox更新】*")
    lines.append(f"- file: `{rel}`")
    lines.append(f"- new_items: {len(items)} (showing {min(len(picked), len(items))})")
    lines.append("")
    if picked:
        for it in picked:
            red = "redacted" if it.redacted else "plain"
            lines.append(f"- key={it.key} kind={it.kind} who={it.who} {red} | {it.text}")
    else:
        lines.append("- (no new items)")

    payload = "\n".join(lines).strip()

    proc = subprocess.run(
        [
            sys.executable,
            str(_slack_notify_path()),
            "--channel",
            channel,
            "--thread-ts",
            thread_ts,
            "--text",
            payload,
            "--print-ts",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"failed to post digest to slack (exit={proc.returncode}): {err[:400]}")
    return (proc.stdout or "").strip()


def _sync_from_thread(
    *,
    channel: str,
    thread_ts: str,
    dd_user: str | None,
    limit: int,
    tmp_dir: Path,
) -> Tuple[list[InboxItem], Dict[str, Any]]:
    tmp = tmp_dir / f"slack_replies_{_hash_key(channel, thread_ts)}.json"
    out = _run_slack_notify_json(
        ["--channel", channel, "--poll-thread", thread_ts, "--poll-limit", str(max(1, int(limit))), "--poll-write-memos"],
        out_json=tmp,
    )
    replies = out.get("replies") if isinstance(out, dict) else None
    items: list[InboxItem] = []
    mapping: Dict[str, Any] = {}

    for msg in (replies or []):
        if not isinstance(msg, dict):
            continue
        if _is_bot_message(msg):
            continue
        user = str(msg.get("user") or msg.get("username") or "").strip()
        if dd_user and user != dd_user:
            continue
        msg_ts = str(msg.get("ts") or "").strip()
        if not msg_ts:
            continue
        raw_text = str(msg.get("text") or "")
        # slack_notify already redacted into msg.text when needed; still apply a belt-and-suspenders pass.
        red_text, redacted = _redact_text(raw_text)
        text = _first_line(red_text, max_len=160)
        kind = _classify_message(text)
        when_iso = _ts_to_iso(msg_ts)

        key = _hash_key(thread_ts, msg_ts, user)
        items.append(
            InboxItem(
                key=key,
                when_iso=when_iso,
                who=("dd" if dd_user and user == dd_user else "human"),
                source="thread",
                kind=kind,
                redacted=redacted,
                text=text,
            )
        )

        mapping[key] = {
            "source": "thread",
            "thread_ts": thread_ts,
            "msg_ts": msg_ts,
            "user": user,
            "text": red_text,
        }

    return items, mapping


def _sync_from_channel_history(
    *,
    channel: str,
    dd_user: str | None,
    limit: int,
    grep: str | None,
    tmp_dir: Path,
) -> Tuple[list[InboxItem], Dict[str, Any]]:
    tmp = tmp_dir / f"slack_history_{_hash_key(channel, str(grep or ''))}.json"
    args = ["--channel", channel, "--history", "--history-limit", str(max(1, int(limit)))]
    if grep:
        args += ["--history-grep", grep]
    out = _run_slack_history_json(args, out_json=tmp)
    messages = out.get("messages") if isinstance(out, dict) else None
    items: list[InboxItem] = []
    mapping: Dict[str, Any] = {}

    for msg in (messages or []):
        if not isinstance(msg, dict):
            continue
        if _is_bot_message(msg):
            continue
        user = str(msg.get("user") or msg.get("username") or "").strip()
        if dd_user and user != dd_user:
            continue
        msg_ts = str(msg.get("ts") or "").strip()
        if not msg_ts:
            continue
        raw_text = str(msg.get("text") or "")
        red_text, redacted = _redact_text(raw_text)
        text = _first_line(red_text, max_len=160)
        kind = _classify_message(text)
        when_iso = _ts_to_iso(msg_ts)

        key = _hash_key("channel", msg_ts, user, text)
        items.append(
            InboxItem(
                key=key,
                when_iso=when_iso,
                who=("dd" if dd_user and user == dd_user else "human"),
                source="channel",
                kind=kind,
                redacted=redacted,
                text=text,
            )
        )

        mapping[key] = {
            "source": "channel",
            "msg_ts": msg_ts,
            "user": user,
            "text": red_text,
        }

    return items, mapping


def cmd_sync(args: argparse.Namespace) -> int:
    channel = str(args.channel or os.getenv("SLACK_CHANNEL") or os.getenv("YTM_SLACK_CHANNEL") or "").strip()
    if not channel:
        raise SystemExit("missing --channel (or env SLACK_CHANNEL)")

    thread_ts_list = [str(x).strip() for x in (args.thread_ts or []) if str(x).strip()]
    dd_user = str(args.dd_user or "").strip() or None

    out_md = Path(str(args.out_md)).resolve() if str(args.out_md or "").strip() else _default_out_md()
    map_path = Path(str(args.map_json)).resolve() if str(args.map_json or "").strip() else _default_map_path()

    tmp_dir = logs_root() / "ops"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    existing_md = _load_inbox_md(out_md)
    existing_keys = _extract_existing_keys(existing_md)
    existing_checks = _extract_existing_checks(existing_md) if out_md.exists() else {}

    fetched_items: list[InboxItem] = []
    new_map_items: Dict[str, Any] = {}

    if thread_ts_list:
        for thread_ts in thread_ts_list:
            items, mapping = _sync_from_thread(
                channel=channel,
                thread_ts=thread_ts,
                dd_user=dd_user,
                limit=int(args.limit),
                tmp_dir=tmp_dir,
            )
            fetched_items.extend(items)
            for k, v in mapping.items():
                if k not in existing_keys:
                    new_map_items[k] = v

    if bool(args.include_history):
        items, mapping = _sync_from_channel_history(
            channel=channel,
            dd_user=dd_user,
            limit=int(args.history_limit),
            grep=(str(args.history_grep or "").strip() or None),
            tmp_dir=tmp_dir,
        )
        fetched_items.extend(items)
        for k, v in mapping.items():
            if k not in existing_keys:
                new_map_items[k] = v

    # Filter + sort newest first.
    include_nonactionable = bool(getattr(args, "include_nonactionable", False))
    filtered = fetched_items if include_nonactionable else [it for it in fetched_items if _actionable(it.kind)]
    filtered_sorted = sorted(filtered, key=lambda x: x.when_iso, reverse=True)

    def _checked_for(key: str) -> bool:
        return bool(existing_checks.get(key) is True)

    new_lines = [it.to_md_line(checked=_checked_for(it.key)) for it in filtered_sorted]

    updated_md = existing_md
    if args.write_ssot:
        updated_md = _replace_inbox_block(existing_md, new_lines)
        out_md.write_text(updated_md, encoding="utf-8")
    elif getattr(args, "post_digest", False):
        raise SystemExit("--post-digest requires --write-ssot (do not ack without updating SSOT)")

    # Update mapping (local, not tracked).
    obj = _load_map(map_path)
    items_obj = obj.get("items") if isinstance(obj.get("items"), dict) else {}
    for k, v in new_map_items.items():
        items_obj[k] = v
    obj["items"] = items_obj
    obj["updated_at"] = _now_iso_utc()
    _save_map(map_path, obj)

    digest_ts = ""
    if getattr(args, "post_digest", False):
        if len(thread_ts_list) != 1:
            raise SystemExit("--post-digest requires exactly one --thread-ts (reply in-thread)")
        new_keys = set(new_map_items.keys())
        new_items = [it for it in filtered_sorted if it.key in new_keys]
        digest_ts = _post_digest_to_slack(
            channel=channel,
            thread_ts=thread_ts_list[0],
            items=new_items,
            out_md=out_md,
            limit=int(getattr(args, "digest_max", 8)),
        )

    summary = {
        "ok": True,
        "now": _now_iso_utc(),
        "fetched": len(fetched_items),
        "written": len(new_lines),
        "out_md": str(out_md.relative_to(ROOT)) if out_md.is_absolute() and str(out_md).startswith(str(ROOT)) else str(out_md),
        "map_json": str(map_path),
        "digest_ts": digest_ts,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Sync Slack messages into a git-friendly PM inbox (digest only).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("sync", help="Fetch Slack thread replies (and optionally channel history) and append digest to SSOT inbox.")
    sp.add_argument("--channel", default="", help="Slack channel (ID or name; default: env SLACK_CHANNEL)")
    sp.add_argument("--thread-ts", action="append", default=[], help="Thread ts to poll (repeatable).")
    sp.add_argument("--dd-user", default="", help="Filter by dd's Slack user id (optional).")
    sp.add_argument("--limit", type=int, default=200, help="Max replies per thread (default: 200).")
    sp.add_argument("--out-md", default="", help="Output markdown path (default: ssot/history/HISTORY_slack_pm_inbox.md)")
    sp.add_argument("--map-json", default="", help="Local mapping JSON path (default: workspaces/logs/ops/slack_pm_inbox_map.json)")
    sp.add_argument("--write-ssot", action="store_true", help="Actually write to SSOT markdown (tracked).")
    sp.add_argument("--post-digest", action="store_true", help="Post a short digest of NEW inbox items back into the same Slack thread.")
    sp.add_argument("--digest-max", type=int, default=8, help="Max items to include in the Slack digest (default: 8).")
    sp.add_argument("--include-nonactionable", action="store_true", help="Include non-actionable chatter (ack/thanks/note).")
    sp.add_argument("--include-history", action="store_true", help="Also include recent channel history messages (optional; can be noisy).")
    sp.add_argument("--history-limit", type=int, default=200, help="Max history messages (default: 200).")
    sp.add_argument("--history-grep", default="", help="Regex filter for history message text (case-insensitive; optional).")
    sp.set_defaults(func=cmd_sync)

    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

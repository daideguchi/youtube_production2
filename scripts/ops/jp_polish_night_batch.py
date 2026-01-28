#!/usr/bin/env python3
from __future__ import annotations

"""
jp_polish_night_batch.py — Ollama JP Polish night batch runner (proposal-only)

SSOT:
- ssot/ops/OPS_SCRIPT_PRE_ANNOTATION_WORKFLOW.md

What this does:
- Scans `workspaces/scripts/**` for A-text inputs updated recently.
- Runs `scripts/ops/jp_polish_propose.py` (proposal-only; never overwrites SoT).
- Appends a nightly summary JSONL:
  `workspaces/scripts/_night_jobs/jp_polish/YYYY-MM-DD.jsonl`
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from _bootstrap import bootstrap

REPO_ROOT = bootstrap(load_env=True)

from factory_common import paths as repo_paths  # noqa: E402


REPORT_SCHEMA = "ytm.ops.jp_polish_night_batch.v1"
RE_CHANNEL = re.compile(r"^CH\d{2}$", flags=re.IGNORECASE)
RE_VIDEO = re.compile(r"^\d{1,3}$")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_today_ymd() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _sha256_path(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _ollama_opener() -> urllib.request.OpenerDirector:
    # Avoid system proxies (corp proxy surprises).
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _ollama_get_json(*, url: str, timeout_sec: float) -> dict[str, Any]:
    opener = _ollama_opener()
    req = urllib.request.Request(url, method="GET")
    with opener.open(req, timeout=float(timeout_sec)) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def _ollama_post_json(*, url: str, payload: dict[str, Any], timeout_sec: float) -> dict[str, Any]:
    opener = _ollama_opener()
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with opener.open(req, timeout=float(timeout_sec)) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def _ollama_precheck(*, base_url: str, timeout_sec: float) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/api/version"
    return _ollama_get_json(url=url, timeout_sec=timeout_sec)


def _ollama_warmup(*, base_url: str, model: str, timeout_sec: float) -> None:
    if not model:
        return
    url = f"{base_url.rstrip('/')}/api/generate"
    _ollama_post_json(
        url=url,
        payload={
            "model": model,
            "system": "",
            "prompt": "OKとだけ返して",
            "stream": False,
            "temperature": 0.0,
        },
        timeout_sec=timeout_sec,
    )


@dataclass(frozen=True)
class EpisodeTarget:
    channel: str
    video: str
    input_path: Path
    out_dir: Path

    @property
    def script_id(self) -> str:
        return f"{self.channel}/{self.video}"


def _parse_videos_arg(raw: str) -> set[str]:
    out: set[str] = set()
    for part in (raw or "").split(","):
        p = part.strip()
        if not p:
            continue
        if not RE_VIDEO.match(p):
            raise SystemExit(f"Invalid --videos entry: {p!r} (expected like 34 or 034)")
        out.add(str(int(p)).zfill(3))
    return out


def _iter_targets(*, channel: Optional[str], only_videos: set[str], since_hours: float) -> Iterable[EpisodeTarget]:
    scripts_root = repo_paths.script_data_root()
    now = time.time()
    since_sec = max(0.0, float(since_hours)) * 3600.0

    for ch_dir in scripts_root.iterdir():
        if not ch_dir.is_dir():
            continue
        ch = ch_dir.name.strip().upper()
        if not RE_CHANNEL.match(ch):
            continue
        if channel and ch != channel:
            continue

        for vid_dir in ch_dir.iterdir():
            if not vid_dir.is_dir():
                continue
            if not RE_VIDEO.match(vid_dir.name.strip()):
                continue
            vid = str(int(vid_dir.name.strip())).zfill(3)
            if only_videos and vid not in only_videos:
                continue

            content_dir = vid_dir / "content"
            in_human = content_dir / "assembled_human.md"
            in_md = content_dir / "assembled.md"
            in_path = in_human if in_human.exists() else in_md
            if not in_path.exists():
                continue

            try:
                st = in_path.stat()
            except Exception:
                continue

            if since_sec > 0 and (now - st.st_mtime) > since_sec:
                continue

            out_dir = content_dir / "analysis" / "jp_polish"
            latest_proposed = out_dir / "proposed_a_text_latest.md"
            try:
                if latest_proposed.exists() and latest_proposed.stat().st_mtime >= st.st_mtime:
                    continue
            except Exception:
                # If stat fails (mount flaky), prefer attempting the job (it will either work or log failure).
                pass

            yield EpisodeTarget(channel=ch, video=vid, input_path=in_path, out_dir=out_dir)


def _jp_polish_propose_script() -> Path:
    return repo_paths.repo_root() / "scripts" / "ops" / "jp_polish_propose.py"


def _run_one(
    *,
    target: EpisodeTarget,
    args: argparse.Namespace,
    summary_path: Path,
) -> dict[str, Any]:
    created_at = _utc_now_iso()
    t0 = time.time()

    input_hash = ""
    try:
        input_hash = _sha256_path(target.input_path)
    except Exception as e:  # noqa: BLE001
        input_hash = f"error:{type(e).__name__}"

    cmd = [
        sys.executable,
        str(_jp_polish_propose_script()),
        "--channel",
        target.channel,
        "--video",
        target.video,
        "--ollama-url",
        str(args.ollama_url),
        "--model",
        str(args.model),
        "--fallback-model",
        str(args.fallback_model),
        "--temperature",
        str(args.temperature),
        "--timeout-sec",
        str(args.timeout_sec),
        "--min-interval-sec",
        str(args.min_interval_sec),
        "--min-len-ratio",
        str(args.min_len_ratio),
        "--max-len-ratio",
        str(args.max_len_ratio),
    ]
    if int(args.max_segments or 0) > 0:
        cmd.extend(["--max-segments", str(int(args.max_segments))])

    proc = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )

    wall_time_s = time.time() - t0
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()

    proposed_path = ""
    if stdout:
        # jp_polish_propose prints the proposed path on the last line.
        proposed_path = stdout.splitlines()[-1].strip()

    status = "ok" if proc.returncode == 0 else "error"

    record: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "created_at": created_at,
        "script_id": target.script_id,
        "channel": target.channel,
        "video": target.video,
        "input_path": str(target.input_path),
        "input_hash": input_hash,
        "out_dir": str(target.out_dir),
        "status": status,
        "model": str(args.model),
        "fallback_model": str(args.fallback_model),
        "wall_time_s": round(float(wall_time_s), 3),
        "returncode": int(proc.returncode),
        "proposed_path": proposed_path,
        "stderr_tail": stderr[-800:] if stderr else "",
        "summary_path": str(summary_path),
    }
    return record


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Night batch runner for jp_polish_propose.py (proposal-only).")
    p.add_argument("--channel", default="", help="Optional filter like CH06 (default: all).")
    p.add_argument("--videos", default="", help="Optional CSV list like 34,35,36 (default: all).")
    p.add_argument("--since-hours", type=float, default=24.0, help="Process inputs modified within N hours.")
    p.add_argument("--max-jobs", type=int, default=0, help="Max jobs to run (0=all matched).")
    p.add_argument("--dry-run", action="store_true", help="List targets and exit (no Ollama calls).")
    p.add_argument("--fail-fast", action="store_true", help="Stop on first failure.")

    p.add_argument("--workspace-root", default="", help="Override YTM_WORKSPACE_ROOT for this run.")

    p.add_argument("--ollama-url", default=os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"))
    p.add_argument("--precheck-timeout-sec", type=float, default=10.0)
    p.add_argument("--warmup", action="store_true")
    p.add_argument("--warmup-timeout-sec", type=float, default=180.0)

    p.add_argument("--model", default="qwen2.5:7b")
    p.add_argument("--fallback-model", default="qwen2.5:1.5b")
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--timeout-sec", type=float, default=60.0)
    p.add_argument("--min-interval-sec", type=float, default=0.2)
    p.add_argument("--min-len-ratio", type=float, default=0.85)
    p.add_argument("--max-len-ratio", type=float, default=1.20)
    p.add_argument("--max-segments", type=int, default=0, help="Forwarded to jp_polish_propose.py (0=all).")

    args = p.parse_args(argv)

    ch_filter = (args.channel or "").strip().upper() or None
    if ch_filter and not RE_CHANNEL.match(ch_filter):
        raise SystemExit(f"Invalid --channel: {args.channel!r} (expected like CH06)")

    only_videos = _parse_videos_arg(args.videos)

    if args.workspace_root:
        os.environ["YTM_WORKSPACE_ROOT"] = str(args.workspace_root)

    # Precheck Ollama
    try:
        version = _ollama_precheck(base_url=str(args.ollama_url), timeout_sec=float(args.precheck_timeout_sec))
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"[jp_polish_night_batch] Ollama precheck failed: {e}") from e

    if args.warmup:
        try:
            _ollama_warmup(
                base_url=str(args.ollama_url),
                model=str(args.model),
                timeout_sec=float(args.warmup_timeout_sec),
            )
        except Exception:
            # Warmup failure should not hard-stop; the first real request may still succeed.
            pass

    targets = list(_iter_targets(channel=ch_filter, only_videos=only_videos, since_hours=float(args.since_hours)))
    if int(args.max_jobs or 0) > 0:
        targets = targets[: int(args.max_jobs)]

    scripts_root = repo_paths.script_data_root()
    summary_dir = scripts_root / "_night_jobs" / "jp_polish"
    _ensure_dir(summary_dir)
    summary_path = summary_dir / f"{_utc_today_ymd()}.jsonl"

    print(f"[jp_polish_night_batch] repo_root: {REPO_ROOT}")
    print(f"[jp_polish_night_batch] workspace_root: {repo_paths.workspace_root()}")
    print(f"[jp_polish_night_batch] ollama: {args.ollama_url} (version={version})")
    print(f"[jp_polish_night_batch] matched: {len(targets)}  summary: {summary_path}")

    if args.dry_run:
        for t in targets:
            print(f"[dry-run] {t.script_id}  input={t.input_path}")
        return 0

    ok = 0
    fail = 0
    with summary_path.open("a", encoding="utf-8") as f:
        for t in targets:
            rec = _run_one(target=t, args=args, summary_path=summary_path)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()

            if rec["status"] == "ok":
                ok += 1
            else:
                fail += 1
                if args.fail_fast:
                    break

    print(f"[jp_polish_night_batch] done: ok={ok} fail={fail}  summary={summary_path}")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())


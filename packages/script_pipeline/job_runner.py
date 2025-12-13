"""
簡易ジョブキュー＋実行ランナー
- add: ジョブをキューに追加
- list: 状態一覧表示
- show: ジョブ詳細
- cancel: pendingをキャンセル
- purge: completed/failed/runningを削除
- retry: failedをpendingに戻す
- run-next: 最初のpendingを1件実行（run-allを内部で起動）
- run-loop: pendingがなくなるか上限まで連続実行

保存先:
  DATA_ROOT/_state/job_queue.jsonl
  各行が1ジョブのJSON（status: pending/running/completed/failed）
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

try:
    from scripts.notifications import send_slack  # type: ignore
except Exception:
    send_slack = None


def _parse_dt(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts))
    except Exception:
        return None


def _duration_sec(started_at: str | None, finished_at: str | None) -> float | None:
    s = _parse_dt(started_at)
    f = _parse_dt(finished_at)
    if s and f:
        return (f - s).total_seconds()
    return None


def _slack_notify(job: Dict[str, Any], result: Dict[str, Any]) -> None:
    webhook = job.get("notify") or os.getenv("SLACK_WEBHOOK_URL")
    if not send_slack or not webhook:
        return
    status = result.get("status")
    attempts = job.get("attempts", 0)
    max_retries = job.get("max_retries", 0)
    rc = result.get("returncode")
    elapsed = _duration_sec(job.get("started_at"), result.get("finished_at"))
    flags = []
    for field in ("stdout", "stderr"):
        msg = (result.get(field) or "").lower()
        if "429" in msg or "resource_exhausted" in msg:
            flags.append("429")
    flag_str = f" flags={','.join(flags)}" if flags else ""
    elapsed_str = f" elapsed={int(elapsed)}s" if elapsed else ""
    title = job.get("title") or ""
    send_slack(
        webhook,
        f"[job_runner] {job['id']} {job['channel']}-{job['video']} status={status} rc={rc} attempts={attempts}/{max_retries}{elapsed_str}{flag_str} {title}",
    )

from .runner import _autoload_env, DATA_ROOT, PROJECT_ROOT

QUEUE_PATH = DATA_ROOT / "_state" / "job_queue.jsonl"
QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_queue() -> List[Dict[str, Any]]:
    if not QUEUE_PATH.exists():
        return []
    lines = QUEUE_PATH.read_text(encoding="utf-8").splitlines()
    jobs: List[Dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            jobs.append(json.loads(line))
        except Exception:
            continue
    return jobs


def _save_queue(jobs: List[Dict[str, Any]]) -> None:
    QUEUE_PATH.write_text("\n".join(json.dumps(j, ensure_ascii=False) for j in jobs) + "\n", encoding="utf-8")


def _acquire_lock() -> None:
    # best-effort file lock (POSIX)
    try:
        import fcntl  # type: ignore
    except Exception:
        return
    fd = os.open(QUEUE_PATH.as_posix(), os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)


def add_job(channel: str, video: str, title: str | None, max_retries: int = 0) -> Dict[str, Any]:
    _autoload_env()
    _acquire_lock()
    jobs = _load_queue()
    job_id = f"{channel.upper()}-{video.zfill(3)}-{int(time.time())}"
    job = {
        "id": job_id,
        "channel": channel.upper(),
        "video": video.zfill(3),
        "title": title or "",
        "status": "pending",
        "created_at": _now(),
        "updated_at": _now(),
        "result": {},
        "attempts": 0,
        "max_retries": max_retries,
        "notify": os.getenv("SLACK_WEBHOOK_URL") or "",
    }
    jobs.append(job)
    _save_queue(jobs)
    return job


def list_jobs() -> List[Dict[str, Any]]:
    _autoload_env()
    _acquire_lock()
    return _load_queue()


def _update_job(jobs: List[Dict[str, Any]], job_id: str, **updates: Any) -> None:
    for j in jobs:
        if j.get("id") == job_id:
            j.update(updates)
            j["updated_at"] = _now()
            break


def run_job(job: Dict[str, Any], max_iter: int = 60) -> Dict[str, Any]:
    _autoload_env()
    channel = job["channel"]
    video = job["video"]
    title = job.get("title") or None
    log_dir = DATA_ROOT / "_state" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{job['id']}.log"
    env = os.environ.copy()
    # fallbackモデル強制オプションは環境変数に委ねる
    cmd = [
        sys.executable,
        "-m",
        "script_pipeline.cli",
        "run-all",
        "--channel",
        channel,
        "--video",
        video,
        "--max-iter",
        str(max_iter),
    ]
    if title:
        cmd.extend(["--title", title])
    # 429フォールバックを有効にするため、環境変数が未設定なら強制ON可
    env.setdefault("SCRIPT_PIPELINE_FORCE_FALLBACK", "1")
    try:
        proc = subprocess.run(cmd, cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, timeout=60 * 60)
        success = proc.returncode == 0
    except subprocess.TimeoutExpired:
        success = False
        proc = None  # type: ignore
    result: Dict[str, Any] = {
        "status": "completed" if success else "failed",
        "stdout": proc.stdout if proc else "",
        "stderr": proc.stderr if proc else "timeout",
        "returncode": proc.returncode if proc else -1,
        "finished_at": _now(),
    }
    try:
        with log_path.open("w", encoding="utf-8") as f:
            f.write(f"$ {' '.join(cmd)}\n")
            f.write(f"started_at: {job.get('started_at', '')}\n")
            f.write(f"finished_at: {result['finished_at']}\n")
            f.write(f"returncode: {result['returncode']}\n\n")
            f.write("STDOUT:\n")
            f.write(result["stdout"] or "")
            f.write("\n\nSTDERR:\n")
            f.write(result["stderr"] or "")
            f.write("\n")
        result["log_path"] = str(log_path)
    except Exception:
        pass
    return result


def run_next(max_iter: int = 60) -> Dict[str, Any] | None:
    _autoload_env()
    _acquire_lock()
    jobs = _load_queue()
    next_job = next((j for j in jobs if j.get("status") == "pending"), None)
    if not next_job:
        return None
    _update_job(jobs, next_job["id"], status="running", started_at=_now())
    _save_queue(jobs)

    result = run_job(next_job, max_iter=max_iter)

    _acquire_lock()
    jobs = _load_queue()
    # retries handling
    attempts = next_job.get("attempts", 0) + 1
    max_retries = next_job.get("max_retries", 0) or 0
    if result["status"] == "failed" and attempts <= max_retries:
        # requeue as pending
        _update_job(
            jobs,
            next_job["id"],
            status="pending",
            attempts=attempts,
            last_result=result,
        )
        next_job.update(result)
        next_job["status"] = "retrying"
        try:
            _slack_notify(
                {**next_job, "attempts": attempts, "max_retries": max_retries},
                {**result, "status": "retrying"},
            )
        except Exception:
            pass
    else:
        _update_job(jobs, next_job["id"], status=result["status"], result=result, attempts=attempts)
        next_job.update(result)
        try:
            _slack_notify(
                {**next_job, "attempts": attempts, "max_retries": max_retries},
                result,
            )
        except Exception:
            pass
    _save_queue(jobs)
    return next_job


def run_loop(limit: int, max_iter: int = 60, sleep_sec: int = 0, max_parallel: int = 1) -> None:
    max_parallel = max(1, max_parallel)

    def worker():
        for _ in range(limit):
            job = run_next(max_iter=max_iter)
            if not job:
                break
            # retrying は成功扱いで継続
            if job.get("status") == "failed":
                # 他のpendingがあれば進む
                if sleep_sec > 0:
                    time.sleep(sleep_sec)
                continue
            if sleep_sec > 0:
                time.sleep(sleep_sec)

    threads: List[threading.Thread] = []
    for _ in range(max_parallel):
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()


def cancel_job(job_id: str) -> bool:
    _acquire_lock()
    jobs = _load_queue()
    for j in jobs:
        if j.get("id") == job_id and j.get("status") == "pending":
            j["status"] = "canceled"
            j["updated_at"] = _now()
            _save_queue(jobs)
            return True
    return False


def force_set(job_id: str, status: str) -> bool:
    _acquire_lock()
    jobs = _load_queue()
    for j in jobs:
        if j.get("id") == job_id:
            j["status"] = status
            j["updated_at"] = _now()
            _save_queue(jobs)
            return True
    return False


def gc_jobs(max_minutes: int = 120) -> int:
    """Mark running jobs older than max_minutes as failed."""
    _acquire_lock()
    jobs = _load_queue()
    changed = 0
    for j in jobs:
        if j.get("status") != "running":
            continue
        started = j.get("started_at")
        if not started:
            continue
        try:
            started_dt = datetime.fromisoformat(str(started))
        except Exception:
            continue
        delta = datetime.now(timezone.utc) - started_dt
        if delta.total_seconds() > max_minutes * 60:
            j["status"] = "failed"
            j["updated_at"] = _now()
            changed += 1
    if changed:
        _save_queue(jobs)
    return changed


def purge_jobs() -> None:
    _acquire_lock()
    jobs = _load_queue()
    jobs = [j for j in jobs if j.get("status") == "pending"]
    _save_queue(jobs)


def retry_job(job_id: str) -> bool:
    _acquire_lock()
    jobs = _load_queue()
    for j in jobs:
        if j.get("id") == job_id and j.get("status") == "failed":
            j["status"] = "pending"
            j["result"] = {}
            j["updated_at"] = _now()
            _save_queue(jobs)
            return True
    return False


def show_job(job_id: str) -> Dict[str, Any] | None:
    jobs = _load_queue()
    for j in jobs:
        if j.get("id") == job_id:
            return j
    return None


def force_set(job_id: str, status: str) -> bool:
    _acquire_lock()
    jobs = _load_queue()
    for j in jobs:
        if j.get("id") == job_id:
            j["status"] = status
            j["updated_at"] = _now()
            _save_queue(jobs)
            return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Simple job queue runner")
    sub = parser.add_subparsers(dest="cmd", required=True)

    add_p = sub.add_parser("add", help="Add a job")
    add_p.add_argument("--channel", required=True)
    add_p.add_argument("--video", required=True)
    add_p.add_argument("--title")
    add_p.add_argument("--max-retries", type=int, default=0, help="Auto-retries on failure")

    list_p = sub.add_parser("list", help="List jobs")
    list_p.add_argument("--json", action="store_true")
    show_p = sub.add_parser("show", help="Show job detail")
    show_p.add_argument("--id", required=True)
    cancel_p = sub.add_parser("cancel", help="Cancel a pending job")
    cancel_p.add_argument("--id", required=True)
    retry_p = sub.add_parser("retry", help="Retry a failed job")
    retry_p.add_argument("--id", required=True)
    sub.add_parser("purge", help="Remove non-pending jobs")
    force_p = sub.add_parser("force-set", help="Force set status (pending/failed) for a job")
    force_p.add_argument("--id", required=True)
    force_p.add_argument("--status", required=True, choices=["pending", "failed"])
    gc_p = sub.add_parser("gc", help="Mark stale running jobs as failed")
    gc_p.add_argument("--max-minutes", type=int, default=120)

    run_p = sub.add_parser("run-next", help="Run next pending job")
    run_p.add_argument("--max-iter", type=int, default=60)
    run_p.add_argument("--max-parallel", type=int, default=1)

    loop_p = sub.add_parser("run-loop", help="Run until queue is empty or failure")
    loop_p.add_argument("--max-iter", type=int, default=60)
    loop_p.add_argument("--limit", type=int, default=20, help="Max jobs to run in one loop")
    loop_p.add_argument("--max-parallel", type=int, default=1)
    loop_p.add_argument("--sleep", type=int, default=0, help="Sleep seconds between jobs")

    args = parser.parse_args()

    if args.cmd == "add":
        job = add_job(args.channel, args.video, args.title, max_retries=args.max_retries)
        print(f"added: {job['id']} {job['channel']}-{job['video']} {job.get('title','')}")
        return

    if args.cmd == "list":
        jobs = list_jobs()
        if getattr(args, "json", False):
            print(json.dumps(jobs, ensure_ascii=False, indent=2))
        else:
            counts = {}
            for j in jobs:
                counts[j.get("status")] = counts.get(j.get("status"), 0) + 1
            print(
                f"jobs: {len(jobs)} pending={counts.get('pending',0)} running={counts.get('running',0)} completed={counts.get('completed',0)} failed={counts.get('failed',0)}"
            )
            for j in jobs:
                print(f"{j.get('status'):10} {j.get('id')} {j.get('channel')}-{j.get('video')} {j.get('title')}")
        return

    if args.cmd == "show":
        job = show_job(args.id)
        if not job:
            print("job not found")
        else:
            print(json.dumps(job, ensure_ascii=False, indent=2))
        return

    if args.cmd == "cancel":
        ok = cancel_job(args.id)
        print("canceled" if ok else "not canceled (only pending can be canceled)")
        return

    if args.cmd == "retry":
        ok = retry_job(args.id)
        print("retried" if ok else "not retried (only failed can be retried)")
        return

    if args.cmd == "purge":
        purge_jobs()
        print("purged non-pending jobs")
        return

    if args.cmd == "force-set":
        ok = force_set(args.id, args.status)
        print("forced" if ok else "not changed")
        return

    if args.cmd == "gc":
        cnt = gc_jobs(max_minutes=args.max_minutes)
        print(f"gc: marked {cnt} running jobs as failed (older than {args.max_minutes} min)")
        return

    if args.cmd == "run-next":
        # max_parallel>1 は run-loop を推奨
        job = run_next(max_iter=args.max_iter)
        if not job:
            print("no pending jobs")
        else:
            print(f"finished {job.get('id')} status={job.get('status')}")
        return

    if args.cmd == "run-loop":
        run_loop(limit=args.limit, max_iter=args.max_iter, sleep_sec=args.sleep, max_parallel=args.max_parallel)
        return


if __name__ == "__main__":
    main()

"""Utility helpers for orchestrating CLI jobs from the Streamlit UI."""

from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from queue import Queue
from typing import Callable, Dict, List, Optional


@dataclass
class JobResult:
    command: List[str]
    returncode: Optional[int]
    started_at: datetime
    finished_at: Optional[datetime]
    log_lines: List[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class Job:
    id: str
    command: List[str]
    cwd: Path
    env: Dict[str, str]
    created_at: datetime
    on_update: Optional[Callable[["Job"], None]] = None
    result: Optional[JobResult] = None


class JobRunner:
    """Run subprocess jobs sequentially, capturing stdout lines for UI updates."""

    def __init__(self) -> None:
        self._queue: "Queue[Job]" = Queue()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def submit(self, job: Job) -> None:
        self._queue.put(job)

    def _worker(self) -> None:
        while True:
            job = self._queue.get()
            try:
                self._execute(job)
            finally:
                self._queue.task_done()

    def _execute(self, job: Job) -> None:
        started_at = datetime.now()
        log_lines: List[str] = []
        try:
            process = subprocess.Popen(
                job.command,
                cwd=str(job.cwd),
                env=job.env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as exc:
            job.result = JobResult(
                command=job.command,
                returncode=None,
                started_at=started_at,
                finished_at=datetime.now(),
                log_lines=[str(exc)],
                error=f"コマンドが見つかりません: {exc}",
            )
            if job.on_update:
                job.on_update(job)
            return

        assert process.stdout is not None
        for line in process.stdout:
            log_lines.append(line.rstrip("\n"))
            if job.on_update:
                job.result = JobResult(
                    command=job.command,
                    returncode=None,
                    started_at=started_at,
                    finished_at=None,
                    log_lines=list(log_lines),
                    error=None,
                )
                job.on_update(job)

        returncode = process.wait()
        job.result = JobResult(
            command=job.command,
            returncode=returncode,
            started_at=started_at,
            finished_at=datetime.now(),
            log_lines=log_lines,
            error=None if returncode == 0 else f"終了コード {returncode}",
        )
        if job.on_update:
            job.on_update(job)

"""Background jobs for the web UI: each job runs the `eichhorn` CLI as a subprocess.

Running the CLI keeps one code path for everything (locks, resume, keep-awake)
and isolates long runs from the web server.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Job:
    id: str
    title: str
    argv: list[str]
    args: list[str] = field(default_factory=list)
    started: float = field(default_factory=time.time)
    finished: float | None = None
    exit_code: int | None = None
    lines: list[str] = field(default_factory=list)
    cond: threading.Condition = field(default_factory=threading.Condition)

    @property
    def running(self) -> bool:
        return self.exit_code is None

    @property
    def state(self) -> str:
        if self.exit_code is None:
            return "running"
        return "ok" if self.exit_code == 0 else "failed"


class JobManager:
    def __init__(self, config_dir: Path | None) -> None:
        self.config_dir = config_dir
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def start(self, title: str, args: list[str]) -> Job:
        argv = [sys.executable, "-m", "eichhorn"]
        if self.config_dir is not None:
            argv += ["--config", str(self.config_dir)]
        argv += args
        job = Job(uuid.uuid4().hex[:12], title, argv, list(args))
        with self._lock:
            self.jobs[job.id] = job
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def _run(self, job: Job) -> None:
        env = {**os.environ, "NO_COLOR": "1", "PYTHONUNBUFFERED": "1"}
        try:
            proc = subprocess.Popen(
                job.argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            self._append(job, f"cannot start: {exc}")
            self._finish(job, 127)
            return
        assert proc.stdout is not None
        for line in proc.stdout:
            self._append(job, line.rstrip("\n"))
        self._finish(job, proc.wait())

    def _append(self, job: Job, line: str) -> None:
        with job.cond:
            job.lines.append(line)
            job.cond.notify_all()

    def _finish(self, job: Job, code: int) -> None:
        with job.cond:
            job.exit_code = code
            job.finished = time.time()
            job.cond.notify_all()

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def recent(self, n: int = 10) -> list[Job]:
        return sorted(self.jobs.values(), key=lambda j: j.started, reverse=True)[:n]

    def busy(self) -> bool:
        return any(j.running for j in self.jobs.values())

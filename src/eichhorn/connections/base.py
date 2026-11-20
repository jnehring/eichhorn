"""The source side of the three layers (spec 2.1)."""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import IO, Any, Protocol


@dataclass(frozen=True)
class SourceEntry:
    """One item found on the host, relative to the source root (with / separators)."""

    rel: str
    type: str  # file | dir | symlink
    size: int = 0
    mtime_ns: int = 0
    mode: int | None = None
    uid: int | None = None
    gid: int | None = None
    target: str | None = None


@dataclass
class ListResult:
    entries: list[SourceEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ConnectionCapabilities:
    shell: bool
    sudo: bool
    rsync: bool  # rsync >= 3.1 is available on the host
    posix_metadata: bool
    bulk: bool  # read_many is cheaper than many open_read calls


@dataclass(frozen=True)
class Command:
    """A command for the host: either an argument list or a shell string."""

    argv: list[str] | None = None
    shell: str | None = None
    sudo: bool = False

    def describe(self) -> str:
        import shlex

        text = self.shell if self.shell is not None else shlex.join(self.argv or [])
        return ("sudo " if self.sudo else "") + text


class Readable(Protocol):
    def read(self, n: int = -1, /) -> bytes: ...

    def __enter__(self) -> Readable: ...

    def __exit__(self, *exc: object) -> None: ...


@dataclass
class Fetched:
    """One file delivered by read_many."""

    rel: str
    stream: IO[bytes] | None  # None for a hardlink to an earlier member
    size: int
    link_to: str | None = None


class Connection(Protocol):
    host: str
    sudo: bool

    @property
    def capabilities(self) -> ConnectionCapabilities: ...

    def popen(
        self, cmd: Command, stdin: int | None = None, stdout: int | None = subprocess.PIPE
    ) -> subprocess.Popen[bytes]: ...

    def listing(self, root: str, excludes: Any) -> ListResult: ...

    def open_read(self, root: str, rel: str, offset: int = 0) -> Readable: ...

    def read_many(self, root: str, rels: list[str]) -> Iterator[Fetched]: ...

    def exists(self, root: str) -> bool: ...


@dataclass
class Completed:
    returncode: int
    stdout: bytes
    stderr: bytes

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def err_text(self, limit: int = 2000) -> str:
        text = (self.stderr or self.stdout).decode("utf-8", "replace").strip()
        return text[-limit:]


def run(
    conn: Connection, cmd: Command, input: bytes | None = None, timeout: float | None = None
) -> Completed:
    """Run a command on the host and capture its output."""
    proc = conn.popen(cmd, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL)
    try:
        out, err = proc.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        return Completed(124, out or b"", (err or b"") + b"\n(timed out)")
    return Completed(proc.returncode, out or b"", err or b"")


class StderrCollector:
    """Reads a process's stderr in a thread, so a full pipe never blocks it."""

    def __init__(self, stream: IO[bytes] | None, limit: int = 64 * 1024) -> None:
        self.data = bytearray()
        self.limit = limit
        self._thread: threading.Thread | None = None
        if stream is not None:
            self._thread = threading.Thread(target=self._read, args=(stream,), daemon=True)
            self._thread.start()

    def _read(self, stream: IO[bytes]) -> None:
        for chunk in iter(lambda: stream.read(8192), b""):
            if len(self.data) < self.limit:
                self.data += chunk[: self.limit - len(self.data)]

    def text(self) -> str:
        if self._thread is not None:
            self._thread.join(timeout=5)
        return self.data.decode("utf-8", "replace").strip()


class ProcessReader:
    """A file-like reader over a process's stdout that checks the exit code on close."""

    def __init__(self, proc: subprocess.Popen[bytes], what: str) -> None:
        self.proc = proc
        self.what = what
        self.stderr = StderrCollector(proc.stderr)

    def read(self, n: int = -1, /) -> bytes:
        assert self.proc.stdout is not None
        return self.proc.stdout.read(n)

    def __enter__(self) -> ProcessReader:
        return self

    def __exit__(self, *exc: object) -> None:
        if self.proc.stdout is not None:
            self.proc.stdout.close()
        code = self.proc.wait()
        if code != 0 and exc[0] is None:
            raise_for(self.stderr.text(), self.what)


def raise_for(stderr: str, what: str) -> None:
    low = stderr.lower()
    if "no such file" in low:
        raise FileNotFoundError(f"{what}: {stderr}")
    if "permission denied" in low:
        raise PermissionError(f"{what}: {stderr}")
    raise OSError(f"{what}: {stderr or 'failed'}")


def filter_excluded(entries: list[SourceEntry], excludes: Any) -> list[SourceEntry]:
    """Apply exclude patterns to a flat, sorted listing, dropping excluded subtrees."""
    out: list[SourceEntry] = []
    pruned: list[str] = []
    for e in sorted(entries, key=lambda e: e.rel):
        if any(e.rel.startswith(p + "/") for p in pruned):
            continue
        if excludes.excluded(e.rel, e.type == "dir"):
            if e.type == "dir":
                pruned.append(e.rel)
            continue
        out.append(e)
    return out


__all__ = [
    "Command",
    "Completed",
    "Connection",
    "ConnectionCapabilities",
    "Fetched",
    "ListResult",
    "ProcessReader",
    "Readable",
    "SourceEntry",
    "StderrCollector",
    "filter_excluded",
    "raise_for",
    "run",
]

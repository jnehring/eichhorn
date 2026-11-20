"""A portable exclusive lock file per repository (spec 3.7)."""

from __future__ import annotations

import contextlib
import json
import os
import socket
import sys
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Any

from eichhorn.errors import LockError

LOCK_NAME = ".lock"


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))  # type: ignore[attr-defined]
        ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_lock(root: Path) -> dict[str, Any] | None:
    try:
        data: dict[str, Any] = json.loads((root / LOCK_NAME).read_text(encoding="utf-8"))
        return data
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"host": "?", "pid": -1, "started": "?", "command": "unreadable lock file"}


def is_stale(info: dict[str, Any]) -> bool:
    return info.get("host") == socket.gethostname() and not pid_alive(int(info.get("pid", -1)))


class RepositoryLock:
    def __init__(self, root: Path, command: str) -> None:
        self.path = root / LOCK_NAME
        self.command = command
        self.held = False

    def acquire(self) -> None:
        info = {
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "started": datetime.now().astimezone().isoformat(timespec="seconds"),
            "command": self.command,
        }
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            other = read_lock(self.path.parent) or {}
            hint = (
                " It looks stale (that process is gone): run `eichhorn repo unlock`."
                if is_stale(other)
                else ""
            )
            raise LockError(
                f"repository {self.path.parent} is locked by {other.get('command', '?')!r} "
                f"(pid {other.get('pid')} on {other.get('host')}, since {other.get('started')})."
                + hint
            ) from None
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(info, fh)
        self.held = True

    def release(self) -> None:
        if self.held:
            with contextlib.suppress(FileNotFoundError):
                self.path.unlink()
            self.held = False

    def __enter__(self) -> RepositoryLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()

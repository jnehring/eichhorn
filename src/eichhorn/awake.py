"""Keep the computer from sleeping while a run is in progress (spec 5.10)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from types import TracebackType


class KeepAwake:
    """Context manager. Every method ends with this process, even after a crash."""

    def __init__(self, enabled: bool = True, reason: str = "Eichhorn backup") -> None:
        self.enabled = enabled
        self.reason = reason
        self._proc: subprocess.Popen[bytes] | None = None
        self.active = False
        self.note = ""

    def __enter__(self) -> KeepAwake:
        if not self.enabled:
            return self
        pid = str(os.getpid())
        try:
            if sys.platform == "darwin" and shutil.which("caffeinate"):
                self._proc = subprocess.Popen(["caffeinate", "-i", "-w", pid])
                self.active = True
            elif sys.platform == "win32":
                import ctypes

                ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
                ctypes.windll.kernel32.SetThreadExecutionState(  # type: ignore[attr-defined]
                    ES_CONTINUOUS | ES_SYSTEM_REQUIRED
                )
                self.active = True
            elif shutil.which("systemd-inhibit") and shutil.which("tail"):
                self._proc = subprocess.Popen(
                    [
                        "systemd-inhibit", "--what=sleep", f"--why={self.reason}",
                        "--who=eichhorn", "--mode=block",
                        "tail", f"--pid={pid}", "-f", "/dev/null",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )  # fmt: skip
                self.active = True
            else:
                self.note = "cannot keep the computer awake on this system"
        except OSError as exc:
            self.note = f"cannot keep the computer awake: {exc}"
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        if self.active and sys.platform == "win32":
            import ctypes

            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)  # type: ignore[attr-defined]
        self.active = False

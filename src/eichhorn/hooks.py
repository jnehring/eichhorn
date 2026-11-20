"""Hooks: shell commands on the host before and after a run (spec 5.1)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from eichhorn.connections.base import Command, Connection, run

HOOK_TIMEOUT = 3600.0


@dataclass
class HookResult:
    phase: str
    command: str
    exit_code: int
    duration_s: float
    output: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def manifest(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "phase": self.phase,
            "command": self.command,
            "exit_code": self.exit_code,
            "duration_s": round(self.duration_s, 2),
        }
        if not self.ok:
            out["output"] = self.output[-1500:]
        return out


def run_phase(
    conn: Connection, phase: str, commands: list[str], sudo: bool, stop_on_error: bool
) -> list[HookResult]:
    results = []
    for text in commands:
        started = time.monotonic()
        res = run(conn, Command(shell=text, sudo=sudo), timeout=HOOK_TIMEOUT)
        output = (res.stdout + b"\n" + res.stderr).decode("utf-8", "replace").strip()
        results.append(HookResult(phase, text, res.returncode, time.monotonic() - started, output))
        if not res.ok and stop_on_error:
            break
    return results

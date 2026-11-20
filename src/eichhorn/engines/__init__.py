"""Choosing a transfer engine (spec 5.2)."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from eichhorn.config import Source
from eichhorn.connections.base import Connection
from eichhorn.engines.builtin import BuiltinEngine
from eichhorn.engines.common import Previous, SourceStats
from eichhorn.errors import EichhornError
from eichhorn.index import Entry
from eichhorn.stores.base import StoreCapabilities
from eichhorn.tools import local_rsync, local_rsync_note


class Engine(Protocol):
    name: str

    def transfer(
        self, conn: Connection, source: Source, dest: Path, prev: Previous
    ) -> tuple[list[Entry], SourceStats]: ...


def rsync_blockers(caps: StoreCapabilities, conn: Connection) -> list[str]:
    """Reasons why the rsync engine cannot be used; empty if it can."""
    reasons = []
    if local_rsync() is None:
        reasons.append(f"local rsync: {local_rsync_note()}")
    if conn.host != "local" and not conn.capabilities.rsync:
        reasons.append(f"no rsync 3.1+ on {conn.host}")
    if not caps.hardlinks:
        reasons.append("the repository has no hardlinks (sparse storage)")
    if not caps.posix_metadata:
        reasons.append("the repository's file system cannot store owners and modes")
    return reasons


def choose_engine(name: str, caps: StoreCapabilities, conn: Connection) -> Engine:
    if name == "builtin":
        return BuiltinEngine(caps)
    blockers = rsync_blockers(caps, conn)
    if name == "rsync":
        if blockers:
            raise EichhornError("engine: rsync is not possible: " + "; ".join(blockers))
        from eichhorn.engines.rsync import RsyncEngine

        return RsyncEngine(caps)
    if not blockers:  # auto
        from eichhorn.engines.rsync import RsyncEngine

        return RsyncEngine(caps)
    return BuiltinEngine(caps)


__all__ = ["BuiltinEngine", "Engine", "Previous", "SourceStats", "choose_engine", "rsync_blockers"]

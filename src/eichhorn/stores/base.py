"""The repository side of the three layers (spec 2.1).

A store holds snapshots. v1 has one implementation, the local file system
(`LocalRepository`). Engines and the run workflow only use this interface, so
another store (e.g. a disk in a remote backup server over SSH) can be added
later.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class StoreCapabilities:
    hardlinks: bool
    case_sensitive: bool
    posix_metadata: bool
    max_file_size: int | None

    @property
    def storage(self) -> str:
        return "hardlinks" if self.hardlinks else "sparse"


class Store(Protocol):
    id: str
    name: str
    root: Path

    @property
    def capabilities(self) -> StoreCapabilities: ...

    def snapshots(self, target: str) -> list[str]: ...

    def latest(self, target: str) -> str | None: ...

    def snapshot_dir(self, target: str, snapshot: str) -> Path: ...

    def manifest(self, target: str, snapshot: str) -> dict[str, Any]: ...

    def new_incomplete(self, target: str, snapshot: str) -> Path: ...

    def incomplete(self, target: str) -> list[Path]: ...

    def finish(self, target: str, incomplete: Path, snapshot: str) -> Path: ...

    def free_bytes(self) -> int: ...

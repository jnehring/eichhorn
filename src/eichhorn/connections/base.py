"""The source side of the three layers (spec 2.1)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import BinaryIO, Protocol


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
    rsync: bool
    posix_metadata: bool


class Connection(Protocol):
    host: str

    @property
    def capabilities(self) -> ConnectionCapabilities: ...

    def list(self, root: str, excludes: object) -> ListResult: ...

    def open_read(self, root: str, rel: str, offset: int = 0) -> BinaryIO: ...

    def exists(self, root: str) -> bool: ...


class SourceGone(Exception):
    """A file vanished or changed between listing and reading (rsync exit 24)."""


__all__ = [
    "Connection",
    "ConnectionCapabilities",
    "Iterator",
    "ListResult",
    "SourceEntry",
    "SourceGone",
]

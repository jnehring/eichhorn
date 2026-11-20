"""Finding a file's content in a repository (spec 3.8).

With hardlink storage every snapshot folder holds every file. With sparse
storage a file's content is in the newest snapshot at or before the one asked
for whose index has the same path with `here: true` and the same hash.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from eichhorn.engines.builtin import PART_SUFFIX
from eichhorn.errors import EichhornError
from eichhorn.index import Entry, load_index
from eichhorn.paths import long_path
from eichhorn.stores.localfs import LocalRepository


def part_paths(snapshot_dir: Path, entry: Entry) -> list[Path]:
    base = snapshot_dir / entry.stored_path
    if entry.parts:
        return [
            long_path(base.with_name(f"{base.name}{PART_SUFFIX}{i:03d}"))
            for i in range(1, entry.parts + 1)
        ]
    return [long_path(base)]


class ContentMissing(EichhornError):
    pass


class Resolver:
    """Index access and content lookup for one target in one repository."""

    def __init__(self, repo: LocalRepository, target: str) -> None:
        self.repo = repo
        self.target = target
        self.snapshots = repo.snapshots(target)
        self.sparse = not repo.capabilities.hardlinks
        self._indexes: dict[str, dict[str, Entry]] = {}
        self._holders: dict[str, list[tuple[str, Entry]]] | None = None

    def resolve_name(self, snapshot: str) -> str:
        if snapshot == "latest":
            latest = self.repo.latest(self.target)
            if latest is None:
                raise EichhornError(f"{self.target} has no snapshots in {self.repo.name}")
            return latest
        if snapshot in self.snapshots:
            return snapshot
        matches = [s for s in self.snapshots if s.startswith(snapshot)]
        if len(matches) == 1:
            return matches[0]
        hint = f"; several match: {', '.join(matches)}" if matches else ""
        raise EichhornError(f"no snapshot {snapshot!r} of {self.target}{hint}")

    def dir(self, snapshot: str) -> Path:
        return self.repo.snapshot_dir(self.target, snapshot)

    def index(self, snapshot: str) -> dict[str, Entry]:
        if snapshot not in self._indexes:
            self._indexes[snapshot] = load_index(self.dir(snapshot))
        return self._indexes[snapshot]

    def forget(self, snapshot: str | None = None) -> None:
        if snapshot is None:
            self._indexes.clear()
        else:
            self._indexes.pop(snapshot, None)
        self._holders = None
        self.snapshots = self.repo.snapshots(self.target)

    def _holder_map(self) -> dict[str, list[tuple[str, Entry]]]:
        if self._holders is None:
            holders: dict[str, list[tuple[str, Entry]]] = {}
            for snap in self.snapshots:
                for e in self.index(snap).values():
                    if e.type == "file" and e.here:
                        holders.setdefault(e.p, []).append((snap, e))
            self._holders = holders
        return self._holders

    def holder(self, snapshot: str, entry: Entry) -> tuple[str, Entry]:
        """The snapshot (and its entry) that physically holds `entry`'s content."""
        if entry.here or not self.sparse:
            return snapshot, entry
        for snap, e in reversed(self._holder_map().get(entry.p, [])):
            if snap <= snapshot and e.sha256 == entry.sha256:
                return snap, e
        raise ContentMissing(
            f"{entry.p}: the content of this version is in no snapshot of {self.target} "
            "(was an older snapshot deleted by hand?)"
        )

    def locate(self, snapshot: str, entry: Entry) -> list[Path]:
        snap, holder = self.holder(snapshot, entry)
        return part_paths(self.dir(snap), holder)

    def chunks(self, snapshot: str, entry: Entry, size: int = 1024 * 1024) -> Iterator[bytes]:
        for path in self.locate(snapshot, entry):
            with open(path, "rb") as fh:
                while data := fh.read(size):
                    yield data

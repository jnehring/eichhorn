"""The file index `metadata.jsonl.gz` (spec 3.5) and `checksums.sha256` (spec 8.1)."""

from __future__ import annotations

import gzip
import json
import os
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

INDEX_NAME = "metadata.jsonl.gz"
CHECKSUMS_NAME = "checksums.sha256"


@dataclass
class Entry:
    p: str  # original path inside the snapshot, e.g. files/docs/a.txt
    type: str  # file | dir | symlink
    size: int = 0
    mtime_ns: int = 0
    sha256: str | None = None
    uid: int | None = None
    gid: int | None = None
    mode: int | None = None
    here: bool = True  # this snapshot folder physically holds the file
    stored: str | None = None  # name on the drive, if different from p
    parts: int | None = None  # number of pieces for split files
    target: str | None = None  # symlink target

    @property
    def stored_path(self) -> str:
        return self.stored or self.p

    def to_json(self) -> str:
        data = {k: v for k, v in asdict(self).items() if v is not None}
        if data.get("here") is True and self.type != "file":
            data.pop("here")
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_json(cls, line: str) -> Entry:
        data = json.loads(line)
        return cls(**data)


def write_index(path: Path, entries: Iterable[Entry]) -> int:
    count = 0
    tmp = path.with_name(path.name + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8", newline="\n") as fh:
        for e in entries:
            fh.write(e.to_json() + "\n")
            count += 1
    os.replace(tmp, path)
    return count


def read_index(path: Path) -> Iterator[Entry]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield Entry.from_json(line)


def load_index(snapshot_dir: Path) -> dict[str, Entry]:
    f = snapshot_dir / INDEX_NAME
    if not f.is_file():
        return {}
    return {e.p: e for e in read_index(f)}


def write_checksums(path: Path, entries: Iterable[Entry]) -> int:
    """sha256sum format, for the files physically stored in this snapshot."""
    count = 0
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        for e in entries:
            if e.type == "file" and e.here and e.sha256 and not e.parts:
                fh.write(f"{e.sha256}  {e.stored_path}\n")
                count += 1
    os.replace(tmp, path)
    return count

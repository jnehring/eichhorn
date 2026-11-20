"""Shared pieces of the transfer engines."""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eichhorn.connections.base import SourceEntry
from eichhorn.index import Entry

CHUNK = 1024 * 1024


@dataclass
class SourceStats:
    name: str
    path: str
    status: str = "ok"
    files: int = 0
    dirs: int = 0
    bytes: int = 0
    transferred_bytes: int = 0
    stored_bytes: int = 0
    new: int = 0
    new_bytes: int = 0
    changed: int = 0
    changed_bytes: int = 0
    unchanged: int = 0
    deleted: int = 0
    deleted_bytes: int = 0
    renamed: int = 0
    split: int = 0
    link_limit_copies: int = 0
    duration_s: float = 0.0
    warnings: list[str] = field(default_factory=list)
    folder_changes: Counter[str] = field(default_factory=Counter)

    def count_fetched(self, entry: Entry, is_new: bool, folder: str, transferred: int) -> None:
        self.transferred_bytes += transferred
        self.stored_bytes += entry.size
        if is_new:
            self.new += 1
            self.new_bytes += entry.size
        else:
            self.changed += 1
            self.changed_bytes += entry.size
        self.folder_changes[folder] += 1

    def count_deleted(self, prev_index: dict[str, Entry], prefix: str, seen: set[str]) -> None:
        for p, old in prev_index.items():
            if old.type == "file" and p.startswith(prefix + "/") and p not in seen:
                self.deleted += 1
                self.deleted_bytes += old.size
                self.folder_changes[parent(p.removeprefix(prefix + "/"))] += 1

    def manifest(self) -> dict[str, Any]:
        top = [
            {"folder": f or "(top level)", "changes": n}
            for f, n in self.folder_changes.most_common(3)
        ]
        return {
            "name": self.name,
            "path": self.path,
            "status": self.status,
            "files": self.files,
            "dirs": self.dirs,
            "bytes": self.bytes,
            "transferred_bytes": self.transferred_bytes,
            "stored_bytes": self.stored_bytes,
            "duration_s": round(self.duration_s, 2),
            "renamed": self.renamed,
            "split": self.split,
            "link_limit_copies": self.link_limit_copies,
            "changes": {
                "new": self.new,
                "new_bytes": self.new_bytes,
                "changed": self.changed,
                "changed_bytes": self.changed_bytes,
                "deleted": self.deleted,
                "deleted_bytes": self.deleted_bytes,
                "unchanged": self.unchanged,
                "top_folders": top,
            },
        }


@dataclass
class Previous:
    """The snapshot to compare with and (with hardlink storage) to link from."""

    dir: Path | None
    index: dict[str, Entry]


def parent(rel: str) -> str:
    return rel.rsplit("/", 1)[0] if "/" in rel else ""


def same(a: Entry, b: SourceEntry) -> bool:
    return a.type == "file" and a.size == b.size and a.mtime_ns == b.mtime_ns


def entry_for(item: SourceEntry, p: str, stored: str | None) -> Entry:
    return Entry(
        p=p,
        type=item.type,
        size=item.size,
        mtime_ns=item.mtime_ns,
        uid=item.uid,
        gid=item.gid,
        mode=item.mode,
        stored=stored if stored != p else None,
        target=item.target,
    )


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()

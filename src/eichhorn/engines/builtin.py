"""The builtin transfer engine (spec 5.2.2): pure Python, works on every OS.

For each source it lists the host, compares with the previous snapshot's index,
hardlinks unchanged files (or, with sparse storage, leaves them out), and copies
new and changed files while hashing them.
"""

from __future__ import annotations

import errno
import hashlib
import os
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from eichhorn.config import Source
from eichhorn.connections.base import Connection, SourceEntry
from eichhorn.excludes import Excludes
from eichhorn.index import Entry
from eichhorn.names import NameMapper, windows_rules_for
from eichhorn.paths import long_path
from eichhorn.stores.base import StoreCapabilities

CHUNK = 1024 * 1024
RESUME_THRESHOLD = 64 * 1024 * 1024  # files above this resume from a byte offset
PARTIAL_SUFFIX = ".eichhorn-partial"
PART_SUFFIX = ".eichhorn-part-"


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

    def manifest(self) -> dict[str, object]:
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


def _parent(rel: str) -> str:
    return rel.rsplit("/", 1)[0] if "/" in rel else ""


def _same(a: Entry, b: SourceEntry) -> bool:
    return a.type == "file" and a.size == b.size and a.mtime_ns == b.mtime_ns


class BuiltinEngine:
    name = "builtin"

    def __init__(self, caps: StoreCapabilities) -> None:
        self.caps = caps

    # ------------------------------------------------------------------
    def transfer(
        self,
        conn: Connection,
        source: Source,
        dest: Path,
        prev: Previous,
        on_progress: object = None,
    ) -> tuple[list[Entry], SourceStats]:
        """Copy one source into `dest` (the .incomplete snapshot folder)."""
        started = time.monotonic()
        stats = SourceStats(source.name, source.path)
        listing = conn.list(source.path, Excludes(source.exclude))
        stats.warnings += listing.warnings
        mapper = NameMapper(windows_rules_for(self.caps.posix_metadata), self.caps.case_sensitive)
        prefix = f"files/{source.name}"
        stored_dirs: dict[str, str] = {"": prefix}
        (long_path(dest / prefix)).mkdir(parents=True, exist_ok=True)
        entries: list[Entry] = [Entry(prefix, "dir")]
        seen: set[str] = set()

        for item in listing.entries:
            parent_rel = _parent(item.rel)
            base_name = item.rel.rsplit("/", 1)[-1]
            parent_stored = stored_dirs.get(parent_rel, prefix)
            stored_name = mapper.stored_name(parent_stored, base_name)
            p = f"{prefix}/{item.rel}"
            stored = f"{parent_stored}/{stored_name}"
            if stored_name != base_name:
                stats.renamed += 1
            seen.add(p)
            entry = Entry(
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
            if item.type == "dir":
                stored_dirs[item.rel] = stored
                long_path(dest / stored).mkdir(parents=True, exist_ok=True)
                stats.dirs += 1
                entries.append(entry)
                continue
            if item.type == "symlink":
                entries.append(entry)  # kept in the index only (spec 3.5)
                continue
            if self._file(conn, source, item, entry, dest, prev, stats):
                stats.files += 1
                stats.bytes += entry.size
                entries.append(entry)

        for p, old in prev.index.items():
            if old.type == "file" and p.startswith(prefix + "/") and p not in seen:
                stats.deleted += 1
                stats.deleted_bytes += old.size
                stats.folder_changes[_parent(p.removeprefix(prefix + "/"))] += 1
        stats.duration_s = time.monotonic() - started
        return entries, stats

    # ------------------------------------------------------------------
    def _file(
        self,
        conn: Connection,
        source: Source,
        item: SourceEntry,
        entry: Entry,
        dest: Path,
        prev: Previous,
        stats: SourceStats,
    ) -> bool:
        old = prev.index.get(entry.p)
        folder = _parent(item.rel)
        max_size = self.caps.max_file_size
        will_split = bool(max_size and item.size > max_size)
        target = long_path(dest / entry.stored_path)

        if old is not None and _same(old, item) and old.sha256:
            entry.sha256 = old.sha256
            stats.unchanged += 1
            if not self.caps.hardlinks:
                entry.here = False  # sparse: stored in an older snapshot (spec 3.8)
                entry.parts = old.parts
                return True
            if self._resumed_ok(target, item, will_split):
                entry.parts = old.parts
                return True
            if prev.dir is not None and self._link(prev.dir, old, dest, entry, stats):
                return True
            # could not link (previous snapshot damaged?): fetch again below
            stats.unchanged -= 1

        is_new = old is None or old.type != "file"
        try:
            if will_split:
                assert max_size is not None
                digest, size, transferred = self._copy_split(conn, source, item, target, max_size)
                entry.parts = -(-size // max_size)
                stats.split += 1
            else:
                digest, size, transferred = self._copy(conn, source, item, target)
        except FileNotFoundError:
            stats.warnings.append(f"vanished while copying: {item.rel}")
            return False
        except PermissionError:
            stats.warnings.append(f"no permission to read: {item.rel}")
            stats.status = "partial"
            return False
        if size != item.size:
            stats.warnings.append(f"changed while copying: {item.rel}")
            entry.size = size
        entry.sha256 = digest
        stats.transferred_bytes += transferred
        stats.stored_bytes += size
        if is_new:
            stats.new += 1
            stats.new_bytes += size
        else:
            stats.changed += 1
            stats.changed_bytes += size
        stats.folder_changes[folder] += 1
        return True

    def _resumed_ok(self, target: Path, item: SourceEntry, split: bool) -> bool:
        """A file left by an interrupted run that is already complete."""
        if split:
            return False
        try:
            st = target.stat()
        except OSError:
            return False
        return st.st_size == item.size and st.st_mtime_ns == item.mtime_ns

    def _link(
        self, prev_dir: Path, old: Entry, dest: Path, entry: Entry, stats: SourceStats
    ) -> bool:
        names = (
            [f"{old.stored_path}{PART_SUFFIX}{i:03d}" for i in range(1, old.parts + 1)]
            if old.parts
            else [old.stored_path]
        )
        new_base = entry.stored_path
        for i, name in enumerate(names, start=1):
            src = long_path(prev_dir / name)
            dst_name = f"{new_base}{PART_SUFFIX}{i:03d}" if old.parts else new_base
            dst = long_path(dest / dst_name)
            try:
                os.link(src, dst)
            except FileExistsError:
                continue
            except OSError as exc:
                if exc.errno == errno.EMLINK:  # too many links (NTFS: 1024)
                    _copy_local(src, dst)
                    stats.link_limit_copies += 1
                    continue
                return False
        entry.parts = old.parts
        return True

    def _copy(
        self, conn: Connection, source: Source, item: SourceEntry, target: Path
    ) -> tuple[str, int, int]:
        h = hashlib.sha256()
        partial = target.with_name(target.name + PARTIAL_SUFFIX)
        sidecar = partial.with_name(partial.name + ".src")  # which source version it holds
        version = f"{item.size} {item.mtime_ns}"
        offset = 0
        if item.size > RESUME_THRESHOLD and partial.exists():
            try:
                same_version = sidecar.read_text(encoding="utf-8") == version
            except OSError:
                same_version = False
            offset = partial.stat().st_size
            if not same_version or offset > item.size:
                offset = 0
            else:
                with open(partial, "rb") as fh:
                    for chunk in iter(lambda: fh.read(CHUNK), b""):
                        h.update(chunk)
        if item.size > RESUME_THRESHOLD:
            sidecar.write_text(version, encoding="utf-8")
        mode = "ab" if offset else "wb"
        transferred = 0
        with conn.open_read(source.path, item.rel, offset) as src, open(partial, mode) as out:
            transferred = _pump(src, out, h)
            out.flush()
            os.fsync(out.fileno())
        size = offset + transferred
        os.replace(partial, target)
        if item.size > RESUME_THRESHOLD:
            sidecar.unlink(missing_ok=True)
        os.utime(target, ns=(item.mtime_ns, item.mtime_ns))
        return h.hexdigest(), size, transferred

    def _copy_split(
        self, conn: Connection, source: Source, item: SourceEntry, target: Path, max_size: int
    ) -> tuple[str, int, int]:
        h = hashlib.sha256()
        size = 0
        part = 0
        with conn.open_read(source.path, item.rel) as src:
            while True:
                part += 1
                part_path = target.with_name(f"{target.name}{PART_SUFFIX}{part:03d}")
                with open(part_path, "wb") as out:
                    written = _pump(src, out, h, limit=max_size)
                if written == 0 and part > 1:
                    part_path.unlink()
                    break
                os.utime(part_path, ns=(item.mtime_ns, item.mtime_ns))
                size += written
                if written < max_size:
                    break
        return h.hexdigest(), size, size


def _pump(src: IO[bytes], out: IO[bytes], h: Any, limit: int | None = None) -> int:
    total = 0
    while True:
        want = CHUNK if limit is None else min(CHUNK, limit - total)
        if want <= 0:
            break
        chunk = src.read(want)
        if not chunk:
            break
        h.update(chunk)
        out.write(chunk)
        total += len(chunk)
    return total


def _copy_local(src: Path, dst: Path) -> None:
    with open(src, "rb") as a, open(dst, "wb") as b:
        while chunk := a.read(CHUNK):
            b.write(chunk)
    st = src.stat()
    os.utime(dst, ns=(st.st_mtime_ns, st.st_mtime_ns))

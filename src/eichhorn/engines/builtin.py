"""The builtin transfer engine (spec 5.2.2): pure Python, works on every OS.

For each source it lists the host, compares with the previous snapshot's index,
hardlinks unchanged files (or, with sparse storage, leaves them out), and then
fetches new and changed files while hashing them: small files in bulk (one tar
stream over SSH), large files one by one so that they can resume.
"""

from __future__ import annotations

import errno
import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from eichhorn import progress
from eichhorn.config import Source
from eichhorn.connections.base import Connection, Readable, SourceEntry
from eichhorn.engines.common import (
    CHUNK,
    Previous,
    SourceStats,
    entry_for,
    parent,
    same,
)
from eichhorn.excludes import Excludes
from eichhorn.index import Entry
from eichhorn.names import NameMapper, windows_rules_for
from eichhorn.paths import long_path
from eichhorn.stores.base import StoreCapabilities

RESUME_THRESHOLD = 64 * 1024 * 1024  # files above this are fetched alone and resume
BATCH_FILES = 5000
BATCH_BYTES = 2 * 1024**3
PARTIAL_SUFFIX = ".eichhorn-partial"
PART_SUFFIX = ".eichhorn-part-"


@dataclass
class _Pending:
    item: SourceEntry
    entry: Entry
    is_new: bool
    target: Path
    done: bool = False


class BuiltinEngine:
    name = "builtin"

    def __init__(self, caps: StoreCapabilities) -> None:
        self.caps = caps

    # ------------------------------------------------------------------
    def transfer(
        self, conn: Connection, source: Source, dest: Path, prev: Previous
    ) -> tuple[list[Entry], SourceStats]:
        """Copy one source into `dest` (the .incomplete snapshot folder)."""
        started = time.monotonic()
        stats = SourceStats(source.name, source.path)
        listing = conn.listing(source.path, Excludes(source.exclude))
        stats.warnings += listing.warnings
        mapper = NameMapper(windows_rules_for(self.caps.posix_metadata), self.caps.case_sensitive)
        prefix = f"files/{source.name}"
        stored_dirs: dict[str, str] = {"": prefix}
        long_path(dest / prefix).mkdir(parents=True, exist_ok=True)
        entries: list[Entry] = [Entry(prefix, "dir")]
        seen: set[str] = set()
        pending: list[_Pending] = []

        for item in listing.entries:
            parent_rel = parent(item.rel)
            base_name = item.rel.rsplit("/", 1)[-1]
            parent_stored = stored_dirs.get(parent_rel, prefix)
            stored_name = mapper.stored_name(parent_stored, base_name)
            p = f"{prefix}/{item.rel}"
            stored = f"{parent_stored}/{stored_name}"
            if stored_name != base_name:
                stats.renamed += 1
            seen.add(p)
            entry = entry_for(item, p, stored)
            if item.type == "dir":
                stored_dirs[item.rel] = stored
                long_path(dest / stored).mkdir(parents=True, exist_ok=True)
                stats.dirs += 1
                entries.append(entry)
                continue
            if item.type == "symlink":
                entries.append(entry)  # kept in the index only (spec 3.5)
                continue
            entries.append(entry)
            if not self._keep_unchanged(item, entry, dest, prev, stats):
                old = prev.index.get(p)
                is_new = old is None or old.type != "file"
                pending.append(_Pending(item, entry, is_new, long_path(dest / entry.stored_path)))

        progress.current().phase(
            "transfer",
            source.name,
            part=progress.current().part,
            bytes_total=sum(pd.item.size for pd in pending),
            files_total=len(pending),
        )
        self._fetch(conn, source, pending, stats)
        dropped = {id(pd.entry) for pd in pending if not pd.done}
        vanished = {e.p for e in entries if id(e) in dropped}
        entries = [e for e in entries if id(e) not in dropped]
        for e in entries:
            if e.type == "file":
                stats.files += 1
                stats.bytes += e.size
        stats.count_deleted(prev.index, prefix, seen - vanished)
        stats.duration_s = time.monotonic() - started
        return entries, stats

    # ------------------------------------------------------------------
    def _keep_unchanged(
        self, item: SourceEntry, entry: Entry, dest: Path, prev: Previous, stats: SourceStats
    ) -> bool:
        """Link (or, sparse, skip) an unchanged file. False means: fetch it."""
        old = prev.index.get(entry.p)
        if old is None or not same(old, item) or not old.sha256:
            return False
        entry.sha256 = old.sha256
        entry.parts = old.parts
        if not self.caps.hardlinks:
            entry.here = False  # sparse: stored in an older snapshot (spec 3.8)
            stats.unchanged += 1
            return True
        target = long_path(dest / entry.stored_path)
        if self._resumed_ok(target, item, bool(old.parts)) or (
            prev.dir is not None and self._link(prev.dir, old, dest, entry, stats)
        ):
            stats.unchanged += 1
            return True
        entry.sha256 = None  # could not link (previous snapshot damaged?): fetch again
        entry.parts = None
        return False

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
        return True

    # ------------------------------------------------------------------
    def _fetch(
        self, conn: Connection, source: Source, pending: list[_Pending], stats: SourceStats
    ) -> None:
        max_size = self.caps.max_file_size
        alone = [
            pd
            for pd in pending
            if pd.item.size > RESUME_THRESHOLD or (max_size and pd.item.size > max_size)
        ]
        alone_ids = {id(pd) for pd in alone}
        bulk = [pd for pd in pending if id(pd) not in alone_ids]

        # small files: in batches, one stream per batch
        batch: list[_Pending] = []
        size = 0
        for pd in bulk:
            batch.append(pd)
            size += pd.item.size
            if len(batch) >= BATCH_FILES or size >= BATCH_BYTES:
                self._fetch_batch(conn, source, batch, stats)
                batch, size = [], 0
        if batch:
            self._fetch_batch(conn, source, batch, stats)

        # large files: one by one, resumable
        for pd in alone:
            try:
                if max_size and pd.item.size > max_size:
                    digest, written = self._copy_split(conn, source, pd, max_size)
                    pd.entry.parts = -(-written // max_size)
                    stats.split += 1
                    transferred = written
                else:
                    digest, written, transferred = self._copy_resumable(conn, source, pd)
            except FileNotFoundError:
                stats.warnings.append(f"vanished while copying: {pd.item.rel}")
                continue
            except PermissionError:
                stats.warnings.append(f"no permission to read: {pd.item.rel}")
                stats.status = "partial"
                continue
            self._done(pd, digest, written, transferred, stats)

    def _fetch_batch(
        self, conn: Connection, source: Source, batch: list[_Pending], stats: SourceStats
    ) -> None:
        by_rel = {pd.item.rel: pd for pd in batch}
        for fetched in conn.read_many(source.path, list(by_rel)):
            pd = by_rel.get(fetched.rel)
            if pd is None or pd.done:
                continue
            if fetched.stream is None:  # hardlink to an earlier member of this stream
                other = by_rel.get(fetched.link_to or "")
                if other is None or not other.done:
                    continue
                with open(other.target, "rb") as fh:
                    digest, written = _write(fh, pd.target, pd.item.mtime_ns)
                self._done(pd, digest, written, 0, stats)
                continue
            digest, written = _write(fetched.stream, pd.target, pd.item.mtime_ns)
            self._done(pd, digest, written, written, stats)
        for pd in batch:
            if not pd.done:
                stats.warnings.append(f"vanished or unreadable: {pd.item.rel}")
        errors = getattr(conn, "last_tar_errors", "")
        if errors:
            stats.warnings += [f"tar: {line}" for line in errors.splitlines()[:20]]

    def _done(
        self, pd: _Pending, digest: str, written: int, transferred: int, stats: SourceStats
    ) -> None:
        if written != pd.item.size:
            stats.warnings.append(f"changed while copying: {pd.item.rel}")
            pd.entry.size = written
        pd.entry.sha256 = digest
        pd.done = True
        progress.current().advance(files=1)
        stats.count_fetched(pd.entry, pd.is_new, parent(pd.item.rel), transferred)

    def _copy_resumable(
        self, conn: Connection, source: Source, pd: _Pending
    ) -> tuple[str, int, int]:
        item, target = pd.item, pd.target
        h = hashlib.sha256()
        partial = target.with_name(target.name + PARTIAL_SUFFIX)
        sidecar = partial.with_name(partial.name + ".src")  # which source version it holds
        version = f"{item.size} {item.mtime_ns}"
        offset = 0
        if partial.exists():
            try:
                same_version = sidecar.read_text(encoding="utf-8") == version
            except OSError:
                same_version = False
            offset = partial.stat().st_size
            if not same_version or offset > item.size:
                offset = 0
            else:
                with open(partial, "rb") as fh:
                    while chunk := fh.read(CHUNK):
                        h.update(chunk)
        sidecar.write_text(version, encoding="utf-8")
        with (
            conn.open_read(source.path, item.rel, offset) as src,
            open(partial, "ab" if offset else "wb") as out,
        ):
            transferred = _pump(src, out, h)
        os.replace(partial, target)
        os.utime(target, ns=(item.mtime_ns, item.mtime_ns))
        sidecar.unlink(missing_ok=True)
        return h.hexdigest(), offset + transferred, transferred

    def _copy_split(
        self, conn: Connection, source: Source, pd: _Pending, max_size: int
    ) -> tuple[str, int]:
        item, target = pd.item, pd.target
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
        return h.hexdigest(), size


def _write(stream: IO[bytes] | Readable, target: Path, mtime_ns: int) -> tuple[str, int]:
    """Write a stream to `target` via a temporary name, hashing it."""
    h = hashlib.sha256()
    partial = target.with_name(target.name + PARTIAL_SUFFIX)
    try:
        with open(partial, "wb") as out:
            written = _pump(stream, out, h)
        os.replace(partial, target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    os.utime(target, ns=(mtime_ns, mtime_ns))
    return h.hexdigest(), written


def _pump(src: Any, out: IO[bytes], h: Any, limit: int | None = None) -> int:
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
        progress.current().advance(len(chunk))
    return total


def _copy_local(src: Path, dst: Path) -> None:
    with open(src, "rb") as a, open(dst, "wb") as b:
        while chunk := a.read(CHUNK):
            b.write(chunk)
    st = src.stat()
    os.utime(dst, ns=(st.st_mtime_ns, st.st_mtime_ns))


__all__ = ["RESUME_THRESHOLD", "BuiltinEngine", "Previous", "SourceStats"]

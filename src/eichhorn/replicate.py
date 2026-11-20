"""Copying snapshots between repositories (spec 5.6 and 5.11).

Used for the further repositories of a run and for `eichhorn repo sync`. The
host is never contacted: the content comes from the source repository.
Unchanged files are hardlinked (or, sparse, left out) against the nearest
older and newer snapshots already in the destination.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eichhorn import cache
from eichhorn import notes as annotations_store
from eichhorn.content import Resolver, part_paths
from eichhorn.engines.builtin import PART_SUFFIX, PARTIAL_SUFFIX
from eichhorn.errors import EichhornError
from eichhorn.index import CHECKSUMS_NAME, INDEX_NAME, Entry, write_checksums, write_index
from eichhorn.lock import RepositoryLock
from eichhorn.names import NameMapper, windows_rules_for
from eichhorn.paths import long_path
from eichhorn.stores.localfs import LocalRepository, write_atomic


@dataclass
class CopyResult:
    target: str
    snapshot: str
    source: str
    destination: str
    linked: int = 0
    copied: int = 0
    copied_bytes: int = 0
    skipped_sparse: int = 0
    duration_s: float = 0.0
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _neighbours(dst: Resolver, snapshot: str) -> list[str]:
    older = [s for s in dst.snapshots if s < snapshot]
    newer = [s for s in dst.snapshots if s > snapshot]
    return ([older[-1]] if older else []) + ([newer[0]] if newer else [])


def _write_from(
    chunks: Any, target: Path, max_size: int | None, mtime_ns: int
) -> tuple[str, int, int | None]:
    """Write content (splitting if needed); returns hash, size and number of parts."""
    h = hashlib.sha256()
    size = 0
    if not max_size:
        tmp = target.with_name(target.name + PARTIAL_SUFFIX)
        with open(tmp, "wb") as fh:
            for c in chunks:
                h.update(c)
                fh.write(c)
                size += len(c)
        os.replace(tmp, target)
        os.utime(target, ns=(mtime_ns, mtime_ns))
        return h.hexdigest(), size, None
    part, written = 0, 0
    out: Any = None
    try:
        for c in chunks:
            h.update(c)
            size += len(c)
            while c:
                if out is None or written >= max_size:
                    if out is not None:
                        out.close()
                    part += 1
                    written = 0
                    out = open(target.with_name(f"{target.name}{PART_SUFFIX}{part:03d}"), "wb")  # noqa: SIM115
                take = c[: max_size - written]
                out.write(take)
                written += len(take)
                c = c[len(take) :]
    finally:
        if out is not None:
            out.close()
    if part == 0:  # empty file
        target.with_name(f"{target.name}{PART_SUFFIX}001").write_bytes(b"")
        part = 1
    if part == 1:  # fits after all: store as a normal file
        os.replace(target.with_name(f"{target.name}{PART_SUFFIX}001"), target)
        os.utime(target, ns=(mtime_ns, mtime_ns))
        return h.hexdigest(), size, None
    return h.hexdigest(), size, part


def copy_snapshot(
    src: Resolver, snapshot: str, dst_repo: LocalRepository, label_from_source: bool = True
) -> CopyResult:
    """Copy one snapshot into `dst_repo` (the caller holds the destination's lock)."""
    started = time.monotonic()
    target = src.target
    result = CopyResult(target, snapshot, src.repo.name, dst_repo.name)
    if dst_repo.snapshot_dir(target, snapshot).exists():
        return result
    caps = dst_repo.capabilities
    dst = Resolver(dst_repo, target)
    neighbours = _neighbours(dst, snapshot)
    older = [s for s in dst.snapshots if s < snapshot]
    incomplete = dst_repo.target_dir(target) / f".incomplete-{snapshot}"
    incomplete.mkdir(parents=True, exist_ok=True)
    mapper = NameMapper(windows_rules_for(caps.posix_metadata), caps.case_sensitive)
    stored_dirs: dict[str, str] = {}
    entries: list[Entry] = []
    for p, e in sorted(src.index(snapshot).items()):
        parent, _, base = p.rpartition("/")
        parent_stored = stored_dirs.get(parent, parent)
        name = mapper.stored_name(parent_stored, base) if parent else base
        stored = f"{parent_stored}/{name}" if parent else name
        new = Entry(**{**e.__dict__, "stored": stored if stored != p else None, "here": True})
        if e.type == "dir":
            stored_dirs[p] = stored
            long_path(incomplete / stored).mkdir(parents=True, exist_ok=True)
            entries.append(new)
            continue
        if e.type != "file":
            entries.append(new)
            continue
        target_path = long_path(incomplete / stored)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if not caps.hardlinks and older:
            prev = dst.index(older[-1]).get(p)
            if prev is not None and prev.type == "file" and prev.sha256 == e.sha256:
                new.here = False
                new.parts = prev.parts
                new.stored = prev.stored
                result.skipped_sparse += 1
                entries.append(new)
                continue
        if caps.hardlinks and _link_from(dst, neighbours, e, incomplete, new):
            result.linked += 1
            entries.append(new)
            continue
        digest, size, parts = _write_from(
            src.chunks(snapshot, e), target_path, caps.max_file_size, e.mtime_ns
        )
        if digest != e.sha256:
            raise EichhornError(
                f"{p}: the copy in {dst_repo.name} does not match the checksum from "
                f"{src.repo.name} – the source drive may be damaged (run eichhorn verify)"
            )
        new.parts = parts
        result.copied += 1
        result.copied_bytes += size
        entries.append(new)
    sdir = src.dir(snapshot)
    has_checksums = (sdir / CHECKSUMS_NAME).exists()
    if has_checksums:
        write_checksums(incomplete / CHECKSUMS_NAME, entries)
    write_index(incomplete / INDEX_NAME, entries)
    manifest: dict[str, Any] = json.loads((sdir / "snapshot.json").read_text(encoding="utf-8"))
    pinned, label, note = annotations_store.effective(
        manifest, annotations_store.load(src.repo, target)
    )
    manifest.update(
        {
            "repository_id": dst_repo.id,
            "repository_name": dst_repo.name,
            "copied_from": src.repo.id,
            "storage": caps.storage,
            "copy": {
                "linked": result.linked,
                "copied": result.copied,
                "copied_bytes": result.copied_bytes,
                "duration_s": round(time.monotonic() - started, 2),
            },
        }
    )
    if label_from_source:
        manifest["label"] = label
        manifest["pinned_at_creation"] = pinned
    write_atomic(incomplete / "snapshot.json", json.dumps(manifest, indent=2, ensure_ascii=False))
    dst_repo.finish(target, incomplete, snapshot)
    if note:
        annotations_store.set_pin(dst_repo, target, snapshot, pinned, note)
    cache.record(dst_repo, target, dst_repo.manifest(target, dst_repo.latest(target) or snapshot))
    result.duration_s = time.monotonic() - started
    return result


def _link_from(
    dst: Resolver, candidates: list[str], e: Entry, incomplete: Path, new: Entry
) -> bool:
    for snap in candidates:
        other = dst.index(snap).get(e.p)
        if other is None or other.type != "file" or other.sha256 != e.sha256 or not other.here:
            continue
        srcs = part_paths(dst.dir(snap), other)
        dsts = part_paths(incomplete, Entry(**{**new.__dict__, "parts": other.parts}))
        try:
            for a, b in zip(srcs, dsts, strict=True):
                if not b.exists():
                    os.link(a, b)
        except OSError:
            for b in dsts:
                b.unlink(missing_ok=True)
            continue
        new.parts = other.parts
        return True
    return False


@dataclass
class SyncPlan:
    target: str
    copy: list[str]
    skipped_older: list[str]


def plan_sync(
    src: LocalRepository, dst: LocalRepository, target: str, include_older: bool
) -> SyncPlan:
    have = set(dst.snapshots(target))
    missing = [s for s in src.snapshots(target) if s not in have]
    oldest = min(have) if have else None
    skipped = [s for s in missing if oldest is not None and s < oldest and not include_older]
    return SyncPlan(target, [s for s in missing if s not in skipped], skipped)


def sync(
    src: LocalRepository,
    dst: LocalRepository,
    targets: list[str],
    include_older: bool,
    dry_run: bool,
) -> tuple[list[SyncPlan], list[CopyResult]]:
    plans = [plan_sync(src, dst, t, include_older) for t in targets]
    results: list[CopyResult] = []
    if dry_run:
        return plans, results
    with RepositoryLock(dst.root, "eichhorn repo sync"):
        for plan in plans:
            if not plan.copy:
                continue
            resolver = Resolver(src, plan.target)
            for snap in plan.copy:
                results.append(copy_snapshot(resolver, snap, dst))
            # pins of snapshots both sides have are copied too (spec 5.11)
            src_notes = annotations_store.load(src, plan.target)
            dst_notes = annotations_store.load(dst, plan.target)
            changed = False
            for snap, item in src_notes.items():
                if snap in dst.snapshots(plan.target) and dst_notes.get(snap) != item:
                    dst_notes[snap] = item
                    changed = True
            if changed:
                annotations_store.save(dst, plan.target, dst_notes)
    return plans, results

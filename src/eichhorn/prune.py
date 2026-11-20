"""Applying retention (spec 7), including the forward moves of sparse storage (spec 3.8)."""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from eichhorn import notes as annotations_store
from eichhorn.content import Resolver, part_paths
from eichhorn.index import CHECKSUMS_NAME, INDEX_NAME, read_index, write_checksums, write_index
from eichhorn.retention import SnapInfo, plan
from eichhorn.stores.localfs import ABANDONED_PREFIX, LocalRepository, write_atomic

JOURNAL = ".prune-journal"
TS_FORMAT = "%Y-%m-%dT%H-%M-%S"


@dataclass
class PruneResult:
    target: str
    repository: str
    keep: dict[str, list[str]] = field(default_factory=dict)
    delete: list[str] = field(default_factory=list)
    moved_files: int = 0
    removed_abandoned: int = 0
    dry_run: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "repository": self.repository,
            "keep": self.keep,
            "delete": self.delete,
            "moved_files": self.moved_files,
            "removed_abandoned": self.removed_abandoned,
            "dry_run": self.dry_run,
        }


def snapshot_infos(repo: LocalRepository, target: str) -> list[SnapInfo]:
    notes = annotations_store.load(repo, target)
    out = []
    for name in repo.snapshots(target):
        m = repo.manifest(target, name)
        pinned, _, _ = annotations_store.effective(m, notes)
        out.append(
            SnapInfo(name, datetime.strptime(name, TS_FORMAT), m.get("status", "ok"), pinned)
        )
    return out


def remove_tree(path: Path) -> None:
    """shutil.rmtree that also removes read-only files (Windows)."""

    def make_writable(func: Any, p: str, _exc: Any) -> None:
        os.chmod(p, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        func(p)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=make_writable)
    else:
        shutil.rmtree(path, onerror=make_writable)


def _apply_journal(repo: LocalRepository, target: str, journal: dict[str, Any]) -> int:
    """Move the listed files from one snapshot into the next kept one. Safe to repeat."""
    src_name, dst_name, paths = journal["from"], journal["to"], journal["paths"]
    src_dir = repo.snapshot_dir(target, src_name)
    dst_dir = repo.snapshot_dir(target, dst_name)
    src_index = (
        {e.p: e for e in read_index(src_dir / INDEX_NAME)}
        if (src_dir / INDEX_NAME).is_file()
        else {}
    )
    dst_entries = list(read_index(dst_dir / INDEX_NAME))
    by_p = {e.p: e for e in dst_entries}
    moved = 0
    wanted = set(paths)
    for p in paths:
        dst_entry = by_p.get(p)
        if dst_entry is None:
            continue
        dsts = part_paths(dst_dir, dst_entry)
        src_entry = src_index.get(p)
        srcs = part_paths(src_dir, src_entry) if src_entry is not None else []
        for i, dst in enumerate(dsts):
            src = srcs[i] if i < len(srcs) else None
            if dst.exists():
                if src is not None and src.exists():
                    src.unlink()
                continue
            if src is None or not src.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, dst)
        if all(d.exists() for d in dsts):
            moved += 1
        else:
            wanted.discard(p)
    for e in dst_entries:
        if e.p in wanted:
            e.here = True
    write_index(dst_dir / INDEX_NAME, dst_entries)
    if (dst_dir / CHECKSUMS_NAME).exists():
        write_checksums(dst_dir / CHECKSUMS_NAME, dst_entries)
    return moved


def recover(repo: LocalRepository, target: str) -> int:
    """Finish a prune that was interrupted (spec 3.8)."""
    f = repo.target_dir(target) / JOURNAL
    if not f.is_file():
        return 0
    journal = json.loads(f.read_text(encoding="utf-8"))
    moved = _apply_journal(repo, target, journal)
    src = repo.snapshot_dir(target, journal["from"])
    if src.exists():
        remove_tree(src)
    f.unlink()
    return moved


def _move_forward(resolver: Resolver, snapshot: str, receiver: str) -> int:
    repo, target = resolver.repo, resolver.target
    mine = resolver.index(snapshot)
    theirs = resolver.index(receiver)
    paths = [
        p
        for p, e in mine.items()
        if e.type == "file"
        and e.here
        and (r := theirs.get(p)) is not None
        and r.type == "file"
        and not r.here
        and r.sha256 == e.sha256
    ]
    if not paths:
        return 0
    journal = {"from": snapshot, "to": receiver, "paths": paths}
    f = repo.target_dir(target) / JOURNAL
    write_atomic(f, json.dumps(journal))
    moved = _apply_journal(repo, target, journal)
    return moved


def prune_target(
    repo: LocalRepository, target: str, rules: dict[str, int], dry_run: bool = False
) -> PruneResult:
    result = PruneResult(target, repo.name, dry_run=dry_run)
    if not dry_run:
        result.moved_files += recover(repo, target)
    infos = snapshot_infos(repo, target)
    result.keep = plan(infos, rules)
    result.delete = [s.name for s in infos if s.name not in result.keep]
    tdir = repo.target_dir(target)
    abandoned = (
        sorted(p for p in tdir.iterdir() if p.name.startswith(ABANDONED_PREFIX))
        if (tdir.is_dir())
        else []
    )
    if dry_run:
        result.removed_abandoned = len(abandoned)
        return result
    resolver = Resolver(repo, target)
    kept = sorted(result.keep)
    for name in result.delete:  # oldest first
        if resolver.sparse:
            receiver = next((k for k in kept if k > name), None)
            if receiver is not None:
                result.moved_files += _move_forward(resolver, name, receiver)
        remove_tree(repo.snapshot_dir(target, name))
        (tdir / JOURNAL).unlink(missing_ok=True)
        resolver.forget()
    for p in abandoned:
        remove_tree(p)
        result.removed_abandoned += 1
    notes = annotations_store.load(repo, target)
    if any(k in notes for k in result.delete):
        annotations_store.save(
            repo, target, {k: v for k, v in notes.items() if k not in result.delete}
        )
    return result

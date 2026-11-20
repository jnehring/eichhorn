"""Integrity checks (spec 8.2)."""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import json
import os
import random
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eichhorn.config import Target
from eichhorn.connections import Command, Connection, LocalConnection, connect
from eichhorn.connections.base import StderrCollector
from eichhorn.content import ContentMissing, Resolver, part_paths
from eichhorn.engines.common import CHUNK
from eichhorn.excludes import Excludes
from eichhorn.index import CHECKSUMS_NAME, INDEX_NAME, Entry
from eichhorn.stores.localfs import write_atomic

META_FILES = {"snapshot.json", CHECKSUMS_NAME, INDEX_NAME}


@dataclass
class VerifyReport:
    target: str
    repository: str
    snapshots: list[str] = field(default_factory=list)
    files: int = 0
    bytes: int = 0
    corrupted: list[dict[str, Any]] = field(default_factory=list)
    missing: list[dict[str, Any]] = field(default_factory=list)
    unexpected: list[dict[str, Any]] = field(default_factory=list)
    source: dict[str, Any] | None = None
    duration_s: float = 0.0
    sample: float | None = None

    @property
    def ok(self) -> bool:
        source_bad = bool(self.source and self.source.get("differs"))
        return not (self.corrupted or self.missing or self.unexpected or source_bad)

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "repository": self.repository,
            "snapshots": self.snapshots,
            "ok": self.ok,
            "files": self.files,
            "bytes": self.bytes,
            "corrupted": self.corrupted,
            "missing": self.missing,
            "unexpected": self.unexpected,
            "source": self.source,
            "duration_s": round(self.duration_s, 2),
            "sample": self.sample,
        }


def _hash_paths(paths: list[Path], is_gzip: bool) -> str:
    h = hashlib.sha256()
    for path in paths:
        with open(path, "rb") as fh:
            while chunk := fh.read(CHUNK):
                h.update(chunk)
    if is_gzip:  # also check that a compressed dump can be read to the end
        with gzip.open(paths[0], "rb") as gz:
            while gz.read(CHUNK):
                pass
    return h.hexdigest()


def _good_copies(resolver: Resolver, entry: Entry, bad_paths: list[Path]) -> list[str]:
    """Snapshots holding the same content in a different physical file."""
    bad = {(p.stat().st_dev, p.stat().st_ino) for p in bad_paths if p.exists()}
    out = []
    for snap in resolver.snapshots:
        e = resolver.index(snap).get(entry.p)
        if e is None or e.sha256 != entry.sha256 or not e.here:
            continue
        paths = part_paths(resolver.dir(snap), e)
        try:
            if any((p.stat().st_dev, p.stat().st_ino) in bad for p in paths):
                continue
        except OSError:
            continue
        out.append(snap)
    return out


def verify(
    resolver: Resolver,
    snapshots: list[str],
    sample: float | None = None,
    seed: int | None = None,
) -> VerifyReport:
    started = time.monotonic()
    report = VerifyReport(resolver.target, resolver.repo.name, list(snapshots), sample=sample)
    done: dict[tuple[int, int], str] = {}  # (dev, inode) of first part → hash
    rng = random.Random(seed)
    for snap in snapshots:
        index = resolver.index(snap)
        files = [e for e in index.values() if e.type == "file" and e.sha256]
        if sample is not None:
            k = max(1, round(len(files) * sample / 100)) if files else 0
            files = rng.sample(files, k)
        for e in files:
            try:
                holder_snap, _ = resolver.holder(snap, e)
                paths = resolver.locate(snap, e)
            except ContentMissing as exc:
                report.missing.append({"snapshot": snap, "path": e.p, "reason": str(exc)})
                continue
            if not all(p.exists() for p in paths):
                report.missing.append({"snapshot": snap, "path": e.p, "stored_in": holder_snap})
                continue
            st = paths[0].stat()
            key = (st.st_dev, st.st_ino)
            actual = done.get(key)
            if actual is None:
                try:
                    actual = _hash_paths(paths, e.p.startswith("dumps/") and e.p.endswith(".gz"))
                except (OSError, EOFError, gzip.BadGzipFile) as exc:
                    actual = f"unreadable: {exc}"
                done[key] = actual
                report.bytes += e.size
            report.files += 1
            if actual != e.sha256:
                report.corrupted.append(
                    {
                        "snapshot": snap,
                        "path": e.p,
                        "stored_in": holder_snap,
                        "expected": e.sha256,
                        "actual": actual,
                        "good_copies_in": _good_copies(resolver, e, paths),
                    }
                )
        if sample is None:
            report.unexpected += _unexpected(resolver, snap, index)
        report.corrupted += _check_delta_dumps(resolver, snap)
    report.duration_s = time.monotonic() - started
    return report


def _unexpected(resolver: Resolver, snap: str, index: dict[str, Entry]) -> list[dict[str, Any]]:
    expected: set[str] = set()
    for e in index.values():
        if e.type == "file" and e.here:
            for p in part_paths(Path(""), e):
                expected.add(p.as_posix())
    base = resolver.dir(snap)
    out = []
    for top in ("files", "dumps"):
        root = base / top
        if not root.is_dir():
            continue
        for dirpath, _dirs, names in os.walk(root):
            for n in names:
                rel = (Path(dirpath) / n).relative_to(base).as_posix()
                if rel not in expected:
                    out.append({"snapshot": snap, "path": rel})
    return out


def hash_on_host(conn: Connection, root: str, rels: list[str]) -> dict[str, str]:
    """sha256 of files on the host (for --against-source)."""
    if not rels:
        return {}
    if isinstance(conn, LocalConnection):
        out = {}
        for rel in rels:
            with contextlib.suppress(OSError):
                out[rel] = _hash_paths([Path(root) / rel], False)
        return out
    shell = f"cd {shlex.quote(root)} && xargs -0 sha256sum -z --"
    proc = conn.popen(Command(shell=shell, sudo=conn.sudo), stdin=subprocess.PIPE)
    errors = StderrCollector(proc.stderr)
    data, _ = proc.communicate(b"\0".join(os.fsencode(r) for r in rels) + b"\0")
    errors.text()
    result = {}
    for line in data.split(b"\0"):
        if len(line) > 66:
            digest, name = line[:64].decode(), os.fsdecode(line[66:])
            result[name] = digest
    return result


def against_source(resolver: Resolver, snapshot: str, target: Target) -> dict[str, Any]:
    conn = connect(target.host, sudo=target.sudo, shell=target.shell)
    index = resolver.index(snapshot)
    out: dict[str, Any] = {
        "snapshot": snapshot,
        "new": 0,
        "gone": 0,
        "changed": 0,
        "same": 0,
        "differs": [],
    }
    for s in target.sources:
        prefix = f"files/{s.name}/"
        listing = conn.listing(s.path, Excludes(s.exclude))
        current = {e.rel: e for e in listing.entries if e.type == "file"}
        stored = {
            p[len(prefix) :]: e
            for p, e in index.items()
            if p.startswith(prefix) and e.type == "file"
        }
        out["new"] += len(set(current) - set(stored))
        out["gone"] += len(set(stored) - set(current))
        same_meta = [
            r
            for r in current
            if r in stored
            and stored[r].size == current[r].size
            and stored[r].mtime_ns == current[r].mtime_ns
        ]
        out["changed"] += sum(1 for r in current if r in stored) - len(same_meta)
        hashes = hash_on_host(conn, s.path, same_meta)
        for rel in same_meta:
            if rel in hashes and hashes[rel] != stored[rel].sha256:
                out["differs"].append(
                    {"path": prefix + rel, "source": hashes[rel], "backup": stored[rel].sha256}
                )
            else:
                out["same"] += 1
    return out


def write_report(resolver: Resolver, report: VerifyReport) -> Path:
    d = resolver.repo.root / "logs" / "verify" / resolver.target
    d.mkdir(parents=True, exist_ok=True)
    name = report.snapshots[-1] if len(report.snapshots) == 1 else f"all-{report.snapshots[-1]}"
    f = d / f"{name}.json"
    data = report.as_dict()
    data["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    write_atomic(f, json.dumps(data, indent=2))
    return f


def last_report(resolver: Resolver) -> dict[str, Any] | None:
    d = resolver.repo.root / "logs" / "verify" / resolver.target
    if not d.is_dir():
        return None
    files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime)
    if not files:
        return None
    data: dict[str, Any] = json.loads(files[-1].read_text(encoding="utf-8"))
    return data


def _check_delta_dumps(resolver: Resolver, snap: str) -> list[dict[str, Any]]:
    """Rebuild every delta dump of a snapshot and compare with the recorded hash."""
    from eichhorn.dumps import rebuild

    problems = []
    try:
        dumps = resolver.repo.manifest(resolver.target, snap).get("dumps", [])
    except Exception:  # a damaged manifest shows up elsewhere
        return []
    for d in dumps:
        delta = d.get("delta")
        if d.get("status") != "ok" or not delta:
            continue
        h = hashlib.sha256()
        try:
            for chunk in rebuild(resolver, snap, d):
                h.update(chunk)
            actual = h.hexdigest()
        except Exception as exc:
            actual = f"cannot rebuild: {exc}"
        if actual != delta.get("rebuilt_sha256"):
            problems.append(
                {
                    "snapshot": snap,
                    "path": f"dumps/{d['name']} (rebuilt from {delta['mode']})",
                    "stored_in": snap,
                    "expected": delta.get("rebuilt_sha256"),
                    "actual": actual,
                    "good_copies_in": [],
                }
            )
    return problems

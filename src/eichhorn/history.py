"""`eichhorn diff` and `eichhorn find` (spec 6)."""

from __future__ import annotations

import fnmatch
from typing import Any

from eichhorn.content import Resolver


def diff(resolver: Resolver, a: str, b: str) -> dict[str, Any]:
    old = resolver.index(a)
    new = resolver.index(b)
    added, removed, changed = [], [], []
    for p, e in new.items():
        if e.type == "dir":
            continue
        o = old.get(p)
        if o is None or o.type != e.type:
            added.append({"path": p, "size": e.size, "type": e.type})
        elif (e.sha256, e.size, e.target) != (o.sha256, o.size, o.target):
            changed.append({"path": p, "old_size": o.size, "new_size": e.size, "type": e.type})
    for p, o in old.items():
        if o.type != "dir" and (p not in new or new[p].type != o.type):
            removed.append({"path": p, "size": o.size, "type": o.type})
    return {
        "from": a,
        "to": b,
        "added": sorted(added, key=lambda x: x["path"]),
        "changed": sorted(changed, key=lambda x: x["path"]),
        "removed": sorted(removed, key=lambda x: x["path"]),
    }


def _matches(pattern: str, path: str) -> bool:
    pat = pattern.lower()
    low = path.lower()
    if "/" not in pat:
        return fnmatch.fnmatchcase(low.rsplit("/", 1)[-1], pat)
    return fnmatch.fnmatchcase(low, pat) or fnmatch.fnmatchcase(low, f"*{pat}")


def find(resolver: Resolver, pattern: str) -> list[dict[str, Any]]:
    """Every matching file with its versions (runs of snapshots with the same content)."""
    found: dict[str, list[dict[str, Any]]] = {}
    for snap in resolver.snapshots:
        for p, e in resolver.index(snap).items():
            if e.type != "file" or not _matches(pattern, p):
                continue
            versions = found.setdefault(p, [])
            if (
                versions
                and versions[-1]["sha256"] == e.sha256
                and versions[-1]["last"] == (_previous(resolver.snapshots, snap))
            ):
                versions[-1]["snapshots"].append(snap)
                versions[-1]["last"] = snap
            else:
                versions.append(
                    {
                        "sha256": e.sha256,
                        "size": e.size,
                        "mtime_ns": e.mtime_ns,
                        "snapshots": [snap],
                        "last": snap,
                    }
                )
    out = []
    for p in sorted(found):
        out.append(
            {
                "path": p,
                "versions": [{k: v for k, v in ver.items() if k != "last"} for ver in found[p]],
            }
        )
    return out


def _previous(snaps: list[str], snap: str) -> str | None:
    i = snaps.index(snap)
    return snaps[i - 1] if i > 0 else None


def versions_of(resolver: Resolver, path: str) -> list[dict[str, Any]]:
    """The versions of one file: runs of snapshots in which it has the same content."""
    versions: list[dict[str, Any]] = []
    prev_snap: str | None = None
    for snap in resolver.snapshots:
        e = resolver.index(snap).get(path)
        if e is None or e.type != "file":
            prev_snap = snap
            continue
        if (
            versions
            and versions[-1]["sha256"] == e.sha256
            and versions[-1]["snapshots"][-1] == (prev_snap)
        ):
            versions[-1]["snapshots"].append(snap)
        else:
            versions.append(
                {"sha256": e.sha256, "size": e.size, "mtime_ns": e.mtime_ns, "snapshots": [snap]}
            )
        prev_snap = snap
    return versions

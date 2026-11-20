"""Statistics from manifests and indexes (spec 9.5). Used by `eichhorn stats` and the web UI."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from eichhorn import notes as annotations_store
from eichhorn.config import Config
from eichhorn.content import Resolver, part_paths
from eichhorn.stores.localfs import LocalRepository
from eichhorn.summary import totals

TS = "%Y-%m-%dT%H-%M-%S"


def _when(name: str) -> datetime:
    return datetime.strptime(name, TS)


def snapshot_stats(resolver: Resolver, snap: str) -> dict[str, Any]:
    repo, target = resolver.repo, resolver.target
    m = repo.manifest(target, snap)
    notes = annotations_store.load(repo, target)
    pinned, label, note = annotations_store.effective(m, notes)
    index = resolver.index(snap)
    files = [e for e in index.values() if e.type == "file"]
    largest = sorted(files, key=lambda e: e.size, reverse=True)[:10]
    t = totals(m)
    transferred = sum(s.get("transferred_bytes", 0) for s in m.get("sources", []))
    transfer_time = m.get("timings", {}).get("transfer", 0) or 0
    from eichhorn.verify import last_report

    verify = last_report(resolver)
    return {
        "target": target,
        "repository": repo.name,
        "snapshot": snap,
        "status": m.get("status"),
        "engine": m.get("engine"),
        "storage": m.get("storage"),
        "host": m.get("host"),
        "label": label,
        "pinned": pinned,
        "note": note,
        "eichhorn_version": m.get("eichhorn_version"),
        "resumed": m.get("resumed", 0),
        "copied_from": m.get("copied_from"),
        "started": m.get("started"),
        "finished": m.get("finished"),
        "files": t["files"],
        "dirs": sum(s.get("dirs", 0) for s in m.get("sources", [])),
        "logical_bytes": t["bytes"],
        "stored_bytes": t["stored_bytes"],
        "freed_by_pruning": freed_by_pruning(resolver, snap),
        "transferred_bytes": transferred,
        "speed_bps": transferred / transfer_time if transfer_time > 0 else None,
        "changes": {k: t[k] for k in ("new", "changed", "deleted")},
        "timings": m.get("timings", {}),
        "sources": m.get("sources", []),
        "dumps": m.get("dumps", []),
        "hooks": m.get("hooks", []),
        "largest": [{"path": e.p, "size": e.size} for e in largest],
        "warnings": m.get("warnings", []),
        "verify": verify if verify and snap in verify.get("snapshots", []) else None,
    }


def freed_by_pruning(resolver: Resolver, snap: str) -> int:
    """Bytes that deleting this snapshot alone would free."""
    index = resolver.index(snap)
    total = 0
    if resolver.sparse:
        later = [s for s in resolver.snapshots if s > snap]
        nxt = resolver.index(later[0]) if later else {}
        for e in index.values():
            if e.type == "file" and e.here:
                other = nxt.get(e.p)
                if other is None or other.sha256 != e.sha256:
                    total += e.size
        return total
    base = resolver.dir(snap)
    for e in index.values():
        if e.type != "file":
            continue
        for p in part_paths(base, e):
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_nlink <= 1:
                total += st.st_size
    return total


def target_series(repo: LocalRepository, target: str) -> list[dict[str, Any]]:
    out = []
    for snap in repo.snapshots(target):
        m = repo.manifest(target, snap)
        t = totals(m)
        out.append(
            {
                "snapshot": snap,
                "status": m.get("status"),
                "files": t["files"],
                "logical_bytes": t["bytes"],
                "stored_bytes": t["stored_bytes"],
                "transferred_bytes": sum(
                    s.get("transferred_bytes", 0) for s in m.get("sources", [])
                ),
                "duration_s": m.get("timings", {}).get("total", 0),
                "new": t["new"],
                "changed": t["changed"],
                "deleted": t["deleted"],
            }
        )
    return out


def repo_stats(repo: LocalRepository, cfg: Config | None = None) -> dict[str, Any]:
    used_by: dict[str, int] = {}
    counts: dict[str, int] = {}
    recent = 0
    oldest: str | None = None
    newest: str | None = None
    horizon = datetime.now() - timedelta(days=90)
    first_seen: datetime | None = None
    for target in repo.targets():
        series = target_series(repo, target)
        counts[target] = len(series)
        used_by[target] = sum(s["stored_bytes"] for s in series)
        for s in series:
            when = _when(s["snapshot"])
            first_seen = when if first_seen is None or when < first_seen else first_seen
            if when >= horizon:
                recent += s["stored_bytes"]
            oldest = s["snapshot"] if oldest is None or s["snapshot"] < oldest else oldest
            newest = s["snapshot"] if newest is None or s["snapshot"] > newest else newest
    free, total = repo.free_bytes(), repo.total_bytes()
    days = max(1.0, (datetime.now() - max(first_seen or horizon, horizon)).total_seconds() / 86400)
    per_day = recent / days if recent else 0.0
    full_in_days = free / per_day if per_day > 0 else None
    return {
        "repository": repo.name,
        "path": str(repo.root),
        "filesystem": repo.meta.get("filesystem"),
        "storage": repo.meta.get("storage"),
        "total_bytes": total,
        "free_bytes": free,
        "used_bytes": total - free,
        "used_by_targets": used_by,
        "snapshots": counts,
        "oldest": oldest,
        "newest": newest,
        "growth_per_day": per_day,
        "full_in_days": full_in_days,
    }

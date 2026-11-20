"""Local index cache (spec 4.1): the last snapshots, also of unplugged drives."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from eichhorn.paths import state_dir
from eichhorn.stores.localfs import LocalRepository, write_atomic


def _file(directory: Path | None = None) -> Path:
    return (directory or state_dir()) / "index.json"


def load(directory: Path | None = None) -> dict[str, Any]:
    try:
        data: dict[str, Any] = json.loads(_file(directory).read_text(encoding="utf-8"))
        return data
    except (OSError, ValueError):
        return {"repositories": {}}


def record(
    repo: LocalRepository, target: str, manifest: dict[str, Any], directory: Path | None = None
) -> None:
    data = load(directory)
    r = data.setdefault("repositories", {}).setdefault(repo.id, {})
    r["name"] = repo.name
    r["last_seen_path"] = str(repo.root)
    r.setdefault("targets", {})[target] = {
        "snapshot": manifest["snapshot"],
        "status": manifest["status"],
        "finished": manifest["finished"],
        "count": len(repo.snapshots(target)),
    }
    f = _file(directory)
    f.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(f, json.dumps(data, indent=2))


def rescan(repo: LocalRepository, directory: Path | None = None) -> int:
    """Rebuild the cache entries of one repository from its manifests."""
    data = load(directory)
    data.setdefault("repositories", {})[repo.id] = {
        "name": repo.name,
        "last_seen_path": str(repo.root),
        "targets": {},
    }
    f = _file(directory)
    f.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(f, json.dumps(data, indent=2))
    n = 0
    for t in repo.targets():
        latest = repo.latest(t)
        if latest:
            record(repo, t, repo.manifest(t, latest), directory)
            n += 1
    return n

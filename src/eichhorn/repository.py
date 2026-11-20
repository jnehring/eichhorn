"""Creating and finding repositories (spec 3.3, 3.6)."""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from eichhorn.config import Config, RepositoryRef
from eichhorn.errors import RepositoryError
from eichhorn.fsinfo import probe
from eichhorn.paths import REPO_DIRNAME, volume_roots
from eichhorn.stores.localfs import FORMAT, REPO_FILE, LocalRepository, write_atomic
from eichhorn.yamlload import dump

README = """\
This drive holds backups made with Eichhorn (https://pypi.org/project/eichhorn/).

Everything here is plain files. You can copy them back without Eichhorn:

  targets/<target>/snapshots/<date>/files/   the backed-up folders
  targets/<target>/snapshots/<date>/dumps/   database dumps (gzip or zstd)
  targets/<target>/latest                    name of the newest snapshot

{storage_text}
Each snapshot has checksums.sha256. Check a snapshot with:
  sha256sum -c checksums.sha256      (Linux)
  shasum -a 256 -c checksums.sha256  (macOS)

Do not change files inside snapshots: unchanged files are shared between them.
More: docs/recovery-without-eichhorn.md in the Eichhorn repository.
"""

STORAGE_TEXT = {
    "hardlinks": (
        "Every snapshot folder is complete. Files that did not change are hardlinks,\n"
        "so they take no extra space.\n"
    ),
    "sparse": (
        "This drive has no hardlinks (exFAT/FAT32), so each snapshot folder holds only\n"
        "the files that were new or changed in that run. The first snapshot is\n"
        "complete. To find an older version of a file, look for it in the snapshot\n"
        "folders from newest to oldest. Files over 4 GiB on FAT32 are split into\n"
        "parts (*.eichhorn-part-001, …); join them with cat or copy /b.\n"
    ),
}


def init_repository(path: Path, name: str, storage: str | None = None) -> LocalRepository:
    path = path.expanduser().absolute()
    if (path / REPO_FILE).exists():
        raise RepositoryError(f"{path} is already a repository")
    if path.exists() and any(p for p in path.iterdir() if p.name != ".DS_Store"):
        raise RepositoryError(f"{path} is not empty; choose an empty or new folder")
    info = probe(path)
    chosen = storage or info.storage
    if chosen == "hardlinks" and not info.hardlinks:
        raise RepositoryError(
            f"{path} ({info.name}) has no hardlinks; only --storage sparse is possible"
        )
    meta = {
        "id": str(uuid.uuid4()),
        "name": name,
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "format": FORMAT,
        "filesystem": info.name,
        "case_sensitive": info.case_sensitive,
        "posix_metadata": info.posix_metadata,
        "storage": chosen,
        "max_file_size": info.max_file_size,
    }
    (path / "targets").mkdir(parents=True, exist_ok=True)
    (path / "logs").mkdir(exist_ok=True)
    write_atomic(path / REPO_FILE, dump(meta))
    write_atomic(path / "README.txt", README.format(storage_text=STORAGE_TEXT[chosen]))
    return LocalRepository(path, meta)


def is_discoverable(path: Path) -> bool:
    """True if automatic discovery (spec 3.6) would find a repository at `path`."""
    return path.name == REPO_DIRNAME and path.parent in volume_roots()


def locate(ref: RepositoryRef) -> LocalRepository | None:
    """Find a connected repository by ID: configured paths first, then all volumes."""
    candidates = [*ref.paths, *(root / REPO_DIRNAME for root in volume_roots())]
    for c in candidates:
        try:
            if (c / REPO_FILE).is_file():
                repo = LocalRepository.open(c)
                if repo.id == ref.id:
                    return repo
        except (OSError, RepositoryError):
            continue
    return None


def connected(cfg: Config) -> list[LocalRepository]:
    return [r for ref in cfg.repositories if (r := locate(ref)) is not None]


def choose(
    cfg: Config, names: list[str] | None, allowed: list[str] | None = None
) -> list[LocalRepository]:
    """Repositories for a run: the named ones, or the single connected one."""
    if names:
        out = []
        for n in names:
            ref = cfg.repository(n)
            repo = locate(ref)
            if repo is None:
                raise RepositoryError(f"repository {ref.name!r} is not connected")
            out.append(repo)
        return out
    conn = connected(cfg)
    if allowed is not None:
        conn = [r for r in conn if r.name in allowed]
    if not conn:
        if not cfg.repositories:
            raise RepositoryError(
                "no repositories configured; create one with `eichhorn repo init PATH --name NAME`"
            )
        raise RepositoryError("no repository is connected; plug in a backup drive")
    if len(conn) > 1:
        names_text = ", ".join(r.name for r in conn)
        raise RepositoryError(
            f"several repositories are connected ({names_text}); choose one with --repo"
        )
    return conn

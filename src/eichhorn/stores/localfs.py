"""Local file system store: a repository on a drive attached to this machine."""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from eichhorn.errors import RepositoryError
from eichhorn.stores.base import StoreCapabilities
from eichhorn.yamlload import dump, load_file

REPO_FILE = "repository.yaml"
FORMAT = 1
SNAPSHOT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}$")
INCOMPLETE_PREFIX = ".incomplete-"
ABANDONED_PREFIX = ".abandoned-"


def write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    replace_with_retry(tmp, path)


def replace_with_retry(src: Path, dst: Path, attempts: int = 5) -> None:
    """os.replace, retried briefly: on Windows antivirus tools can hold files."""
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.2 * (i + 1))


class LocalRepository:
    def __init__(self, root: Path, meta: dict[str, Any]) -> None:
        self.root = root
        self.meta = meta
        self.id: str = meta["id"]
        self.name: str = meta["name"]

    # ---- opening -------------------------------------------------------
    @classmethod
    def open(cls, root: Path) -> LocalRepository:
        f = root / REPO_FILE
        if not f.is_file():
            raise RepositoryError(f"{root} is not an Eichhorn repository (no {REPO_FILE})")
        meta = load_file(f).data or {}
        if not isinstance(meta, dict) or "id" not in meta or "name" not in meta:
            raise RepositoryError(f"{f} is damaged: id and name are required")
        if int(meta.get("format", FORMAT)) > FORMAT:
            raise RepositoryError(
                f"{root} uses storage format {meta['format']}; this Eichhorn understands "
                f"up to {FORMAT}. Please update Eichhorn."
            )
        return cls(root, meta)

    @property
    def capabilities(self) -> StoreCapabilities:
        m = self.meta
        return StoreCapabilities(
            hardlinks=m.get("storage", "hardlinks") == "hardlinks",
            case_sensitive=bool(m.get("case_sensitive", True)),
            posix_metadata=bool(m.get("posix_metadata", True)),
            max_file_size=m.get("max_file_size"),
        )

    # ---- layout --------------------------------------------------------
    def target_dir(self, target: str) -> Path:
        return self.root / "targets" / target

    def snapshots_dir(self, target: str) -> Path:
        return self.target_dir(target) / "snapshots"

    def snapshot_dir(self, target: str, snapshot: str) -> Path:
        return self.snapshots_dir(target) / snapshot

    def targets(self) -> list[str]:
        base = self.root / "targets"
        return sorted(p.name for p in base.iterdir() if p.is_dir()) if base.is_dir() else []

    def snapshots(self, target: str) -> list[str]:
        d = self.snapshots_dir(target)
        if not d.is_dir():
            return []
        return sorted(
            p.name
            for p in d.iterdir()
            if SNAPSHOT_RE.match(p.name) and (p / "snapshot.json").is_file()
        )

    def latest(self, target: str) -> str | None:
        f = self.target_dir(target) / "latest"
        try:
            name = f.read_text(encoding="utf-8").strip()
            if (
                SNAPSHOT_RE.match(name)
                and (self.snapshot_dir(target, name) / "snapshot.json").is_file()
            ):
                return name
        except OSError:
            pass
        snaps = self.snapshots(target)  # `latest` missing or stale: fall back to the newest
        return snaps[-1] if snaps else None

    def manifest(self, target: str, snapshot: str) -> dict[str, Any]:
        f = self.snapshot_dir(target, snapshot) / "snapshot.json"
        try:
            data: dict[str, Any] = json.loads(f.read_text(encoding="utf-8"))
            return data
        except (OSError, ValueError) as exc:
            raise RepositoryError(f"cannot read {f}: {exc}") from exc

    def incomplete(self, target: str) -> list[Path]:
        d = self.target_dir(target)
        if not d.is_dir():
            return []
        return sorted(p for p in d.iterdir() if p.name.startswith(INCOMPLETE_PREFIX))

    def new_incomplete(self, target: str, snapshot: str) -> Path:
        p = self.target_dir(target) / f"{INCOMPLETE_PREFIX}{snapshot}"
        p.mkdir(parents=True, exist_ok=False)
        return p

    def abandon(self, incomplete: Path) -> Path:
        dst = incomplete.with_name(
            ABANDONED_PREFIX + incomplete.name.removeprefix(INCOMPLETE_PREFIX)
        )
        replace_with_retry(incomplete, dst)
        return dst

    def finish(self, target: str, incomplete: Path, snapshot: str) -> Path:
        dst = self.snapshot_dir(target, snapshot)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            raise RepositoryError(f"snapshot {dst} already exists")
        replace_with_retry(incomplete, dst)
        write_atomic(self.target_dir(target) / "latest", snapshot + "\n")
        return dst

    def free_bytes(self) -> int:
        return shutil.disk_usage(self.root).free

    def total_bytes(self) -> int:
        return shutil.disk_usage(self.root).total

    def logs_dir(self) -> Path:
        d = self.root / "logs"
        d.mkdir(exist_ok=True)
        return d

    def save_meta(self) -> None:
        write_atomic(self.root / REPO_FILE, dump(self.meta))

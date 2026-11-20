"""Connection to folders on this computer."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import BinaryIO

from eichhorn.connections.base import ConnectionCapabilities, ListResult, SourceEntry
from eichhorn.excludes import Excludes
from eichhorn.paths import long_path


class LocalConnection:
    host = "local"

    @property
    def capabilities(self) -> ConnectionCapabilities:
        return ConnectionCapabilities(
            shell=True, sudo=False, rsync=False, posix_metadata=sys.platform != "win32"
        )

    def exists(self, root: str) -> bool:
        return Path(root).is_dir()

    def list(self, root: str, excludes: object) -> ListResult:
        assert isinstance(excludes, Excludes)
        result = ListResult()
        base = long_path(Path(root))

        def walk(directory: Path, rel_dir: str) -> None:
            try:
                with os.scandir(directory) as it:
                    items = sorted(it, key=lambda e: e.name)
            except OSError as exc:
                result.warnings.append(f"cannot read folder {rel_dir or '.'}: {exc.strerror}")
                return
            for item in items:
                rel = f"{rel_dir}/{item.name}" if rel_dir else item.name
                try:
                    st = item.stat(follow_symlinks=False)
                except OSError as exc:
                    result.warnings.append(f"cannot stat {rel}: {exc.strerror}")
                    continue
                is_dir = stat.S_ISDIR(st.st_mode)
                if excludes.excluded(rel, is_dir):
                    continue
                mode = stat.S_IMODE(st.st_mode)
                uid: int | None = getattr(st, "st_uid", None)
                gid: int | None = getattr(st, "st_gid", None)
                mt = st.st_mtime_ns
                if stat.S_ISLNK(st.st_mode):
                    try:
                        target = os.readlink(item.path)
                    except OSError:
                        target = ""
                    result.entries.append(
                        SourceEntry(rel, "symlink", 0, mt, mode, uid, gid, target)
                    )
                elif is_dir:
                    result.entries.append(SourceEntry(rel, "dir", 0, mt, mode, uid, gid))
                    walk(Path(item.path), rel)
                elif stat.S_ISREG(st.st_mode):
                    result.entries.append(SourceEntry(rel, "file", st.st_size, mt, mode, uid, gid))
                else:
                    result.warnings.append(f"skipped {rel}: not a regular file, folder or link")

        if not base.is_dir():
            raise FileNotFoundError(f"source folder {root} does not exist")
        walk(base, "")
        return result

    def open_read(self, root: str, rel: str, offset: int = 0) -> BinaryIO:
        fh = open(long_path(Path(root) / rel), "rb")  # noqa: SIM115 - caller closes
        if offset:
            fh.seek(offset)
        return fh

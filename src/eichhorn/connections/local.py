"""Connection to folders on this computer."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from eichhorn.connections.base import (
    Command,
    ConnectionCapabilities,
    Fetched,
    ListResult,
    Readable,
    SourceEntry,
)
from eichhorn.paths import long_path
from eichhorn.tools import local_rsync


class _FileReader:
    def __init__(self, path: Path, offset: int) -> None:
        self.fh = open(path, "rb")  # noqa: SIM115 - closed in __exit__
        if offset:
            self.fh.seek(offset)

    def read(self, n: int = -1, /) -> bytes:
        return self.fh.read(n)

    def __enter__(self) -> _FileReader:
        return self

    def __exit__(self, *exc: object) -> None:
        self.fh.close()


class LocalConnection:
    host = "local"

    def __init__(self, sudo: bool = False, shell: str | None = None) -> None:
        self.sudo = sudo
        self.shell = shell  # cmd | powershell on Windows

    @property
    def capabilities(self) -> ConnectionCapabilities:
        return ConnectionCapabilities(
            shell=True,
            sudo=sys.platform != "win32",
            rsync=local_rsync() is not None,
            posix_metadata=sys.platform != "win32",
            bulk=False,
        )

    # ---- commands ----------------------------------------------------
    def argv_for(self, cmd: Command) -> list[str]:
        if cmd.shell is not None:
            if sys.platform == "win32":
                if self.shell == "powershell":
                    argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd.shell]
                else:
                    argv = ["cmd", "/c", cmd.shell]
            else:
                argv = ["sh", "-c", cmd.shell]
        else:
            argv = list(cmd.argv or [])
        if cmd.sudo and sys.platform != "win32":
            argv = ["sudo", "-n", *argv]
        return argv

    def popen(
        self, cmd: Command, stdin: int | None = None, stdout: int | None = subprocess.PIPE
    ) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            self.argv_for(cmd), stdin=stdin, stdout=stdout, stderr=subprocess.PIPE
        )

    # ---- files -------------------------------------------------------
    def exists(self, root: str) -> bool:
        return Path(root).is_dir()

    def listing(self, root: str, excludes: Any) -> ListResult:
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

    def open_read(self, root: str, rel: str, offset: int = 0) -> Readable:
        return _FileReader(long_path(Path(root) / rel), offset)

    def read_many(self, root: str, rels: list[str]) -> Iterator[Fetched]:
        for rel in rels:
            path = long_path(Path(root) / rel)
            try:
                fh = open(path, "rb")  # noqa: SIM115 - closed below
            except FileNotFoundError:
                continue  # vanished; the engine notices the missing file
            with fh:
                yield Fetched(rel, fh, os.fstat(fh.fileno()).st_size)

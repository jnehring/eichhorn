"""Getting files back out of a snapshot (spec 6, `eichhorn restore`)."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import tarfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eichhorn.connections import Command, SshConnection, run
from eichhorn.connections.base import StderrCollector
from eichhorn.content import Resolver
from eichhorn.errors import EichhornError
from eichhorn.index import Entry
from eichhorn.paths import long_path

REMOTE_RE = re.compile(r"^(?P<host>[^/\\:]{2,}):(?P<path>/.*)$")


@dataclass
class RestoreResult:
    files: int = 0
    dirs: int = 0
    symlinks: int = 0
    bytes: int = 0
    destination: str = ""
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def select(index: dict[str, Entry], path: str | None) -> list[tuple[str, Entry]]:
    """Entries under `path`, with the relative name each gets at the destination."""
    if not path:
        chosen = [
            (e.p.removeprefix("files/"), e)
            for e in index.values()
            if e.p.startswith(("files/", "dumps"))
        ]
        return sorted((r, e) for r, e in chosen if r and e.p != "files")
    sel = path.strip("/")
    if not sel.startswith(("files/", "dumps")):  # "docs/a.txt" means "files/docs/a.txt"
        candidate = f"files/{sel}"
        if candidate in index or any(p.startswith(candidate + "/") for p in index):
            sel = candidate
    base = sel.rsplit("/", 1)[0] if "/" in sel else ""
    out = []
    for p, e in index.items():
        if p == sel or p.startswith(sel + "/"):
            rel = p[len(base) + 1 :] if base else p
            out.append((rel, e))
    if not out:
        raise EichhornError(f"{path!r} is not in this snapshot (see `eichhorn find`)")
    return sorted(out)


def parse_destination(dest: str) -> tuple[str | None, str]:
    m = REMOTE_RE.match(dest)
    if m and len(m.group("host")) != 1:  # not a Windows drive letter
        return m.group("host"), m.group("path")
    return None, dest


def restore_local(
    resolver: Resolver, snapshot: str, items: list[tuple[str, Entry]], dest: Path, force: bool
) -> RestoreResult:
    result = RestoreResult(destination=str(dest))
    if dest.exists() and any(dest.iterdir()) and not force:
        conflicts = [r for r, e in items if e.type != "dir" and (dest / r).exists()]
        if conflicts:
            raise EichhornError(
                f"{dest} already contains {len(conflicts)} of these files (e.g. {conflicts[0]}); "
                "use --force to overwrite them, or choose an empty folder"
            )
    posix = sys.platform != "win32"
    as_root = posix and os.geteuid() == 0
    dirs: list[tuple[Path, Entry]] = []
    dest.mkdir(parents=True, exist_ok=True)
    for rel, e in items:
        target = long_path(dest / rel)
        if e.type == "dir":
            target.mkdir(parents=True, exist_ok=True)
            dirs.append((target, e))
            result.dirs += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if e.type == "symlink":
            if not posix:
                result.warnings.append(f"symlink not created on Windows: {rel} -> {e.target}")
                continue
            if target.is_symlink() or target.exists():
                if not force:
                    continue
                target.unlink()
            os.symlink(e.target or "", target)
            result.symlinks += 1
            continue
        tmp = target.with_name(target.name + ".eichhorn-restore")
        with open(tmp, "wb") as out:
            for chunk in resolver.chunks(snapshot, e):
                out.write(chunk)
        os.replace(tmp, target)
        if posix and e.mode is not None:
            os.chmod(target, e.mode)
        if as_root and e.uid is not None and e.gid is not None:
            os.chown(target, e.uid, e.gid)
        if e.mtime_ns:
            os.utime(target, ns=(e.mtime_ns, e.mtime_ns))
        result.files += 1
        result.bytes += e.size
    for target, e in reversed(dirs):  # after the files, so their times stay
        if posix and e.mode is not None:
            os.chmod(target, e.mode | 0o700)
        if as_root and e.uid is not None and e.gid is not None:
            os.chown(target, e.uid, e.gid)
        if e.mtime_ns:
            os.utime(target, ns=(e.mtime_ns, e.mtime_ns))
    return result


class _ChunkReader:
    def __init__(self, it: Any) -> None:
        self.it = it
        self.buf = b""

    def read(self, n: int = -1) -> bytes:
        while n < 0 or len(self.buf) < n:
            try:
                self.buf += next(self.it)
            except StopIteration:
                break
        if n < 0:
            data, self.buf = self.buf, b""
        else:
            data, self.buf = self.buf[:n], self.buf[n:]
        return data


def restore_remote(
    resolver: Resolver,
    snapshot: str,
    items: list[tuple[str, Entry]],
    host: str,
    path: str,
    force: bool,
    sudo: bool,
) -> RestoreResult:
    """Stream a tar archive to the host and unpack it there, with owners and modes."""
    conn = SshConnection(host, sudo=sudo)
    result = RestoreResult(destination=f"{host}:{path}")
    q = shlex.quote(path)
    if not force:
        check = run(conn, Command(shell=f'[ -d {q} ] && [ -n "$(ls -A {q})" ]', sudo=sudo))
        if check.ok:
            raise EichhornError(f"{host}:{path} is not empty; use --force to unpack into it")
    shell = f"mkdir -p {q} && tar -C {q} -xpf -"
    proc = conn.popen(Command(shell=shell, sudo=sudo), stdin=subprocess.PIPE)
    errors = StderrCollector(proc.stderr)
    out_thread = threading.Thread(target=lambda: proc.stdout and proc.stdout.read(), daemon=True)
    out_thread.start()
    assert proc.stdin is not None
    try:
        with tarfile.open(fileobj=proc.stdin, mode="w|", format=tarfile.PAX_FORMAT) as tf:
            for rel, e in items:
                info = tarfile.TarInfo(rel)
                info.mtime = e.mtime_ns / 1e9 if e.mtime_ns else 0
                info.mode = e.mode if e.mode is not None else (0o755 if e.type == "dir" else 0o644)
                info.uid = e.uid or 0
                info.gid = e.gid or 0
                if e.type == "dir":
                    info.type = tarfile.DIRTYPE
                    tf.addfile(info)
                    result.dirs += 1
                elif e.type == "symlink":
                    info.type = tarfile.SYMTYPE
                    info.linkname = e.target or ""
                    tf.addfile(info)
                    result.symlinks += 1
                else:
                    info.size = e.size
                    tf.addfile(info, _ChunkReader(resolver.chunks(snapshot, e)))
                    result.files += 1
                    result.bytes += e.size
        proc.stdin.close()
    except BrokenPipeError:
        pass
    code = proc.wait()
    out_thread.join(timeout=5)
    if code != 0:
        raise EichhornError(f"unpacking on {host} failed: {errors.text()[-1000:]}")
    return result

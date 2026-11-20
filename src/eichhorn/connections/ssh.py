"""Connection to a Linux/Unix host over the system's OpenSSH client (spec 5.2.2, 5.5).

Only standard tools are needed on the host: sh, find (GNU, with -printf) or
python3, tar, tail and cat; rsync for the rsync engine.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tarfile
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from functools import cached_property
from pathlib import Path
from typing import Any

from eichhorn.connections.base import (
    Command,
    ConnectionCapabilities,
    Fetched,
    ListResult,
    ProcessReader,
    Readable,
    SourceEntry,
    StderrCollector,
    filter_excluded,
    raise_for,
    run,
)
from eichhorn.errors import EichhornError
from eichhorn.tools import RSYNC_MIN, parse_rsync_version

TYPES = {"f": "file", "d": "dir", "l": "symlink"}
FIND_FORMAT = r"%y\0%s\0%T@\0%m\0%U\0%G\0%l\0%P\0"

# Used when the host has no GNU find. Prints the same NUL-separated fields.
PY_LISTER = r"""
import os, stat, sys
root = sys.argv[1]
out = sys.stdout.buffer
def emit(*fields):
    for f in fields:
        out.write(os.fsencode(str(f)) + b"\0")
for d, dirs, files in os.walk(root):
    for name in dirs + files:
        p = os.path.join(d, name)
        try:
            st = os.lstat(p)
        except OSError:
            continue
        m = st.st_mode
        t = "d" if stat.S_ISDIR(m) else "l" if stat.S_ISLNK(m) else "f" if stat.S_ISREG(m) else "o"
        link = os.readlink(p) if t == "l" else ""
        emit(t, st.st_size, "%d.%09d" % divmod(st.st_mtime_ns, 10**9), "%o" % stat.S_IMODE(m),
             st.st_uid, st.st_gid, link, os.path.relpath(p, root))
"""

PROBE = r"""
echo "printf=$(find / -maxdepth 0 -printf '' >/dev/null 2>&1 && echo yes)"
echo "python3=$(command -v python3)"
echo "tar=$(command -v tar)"
echo "rsync=$(rsync --version 2>/dev/null | head -n 1)"
echo "uname=$(uname -s)"
echo "user=$(id -un)"
for t in find tar rsync docker sh cat tail python3; do echo "path_$t=$(command -v $t)"; done
"""


@dataclass
class HostInfo:
    gnu_find: bool
    python3: bool
    tar: bool
    rsync_version: tuple[int, int, int] | None
    system: str
    user: str
    paths: dict[str, str]

    @property
    def rsync_ok(self) -> bool:
        return self.rsync_version is not None and self.rsync_version[:2] >= RSYNC_MIN


def ssh_base() -> list[str]:
    """The ssh command with Eichhorn's options. $EICHHORN_SSH replaces `ssh` (tests)."""
    override = os.environ.get("EICHHORN_SSH")
    argv = shlex.split(override) if override else ["ssh"]
    argv += ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=30"]
    if sys.platform != "win32" and not override:
        # reuse one connection for the many short commands of a run
        control = Path("/tmp") / f"eichhorn-{os.getuid()}"
        control.mkdir(mode=0o700, exist_ok=True)
        argv += [
            "-o", "ControlMaster=auto",
            "-o", f"ControlPath={control}/%C",
            "-o", "ControlPersist=120",
        ]  # fmt: skip
    return argv


class SshConnection:
    def __init__(self, host: str, sudo: bool = False) -> None:
        self.host = host
        self.sudo = sudo
        self.last_tar_errors = ""

    # ---- commands ----------------------------------------------------
    def remote_string(self, cmd: Command) -> str:
        if cmd.shell is not None:
            text = cmd.shell
            if cmd.sudo:
                text = "sudo -n sh -c " + shlex.quote(text)
        else:
            text = shlex.join(cmd.argv or [])
            if cmd.sudo:
                text = "sudo -n " + text
        return text

    def argv_for(self, cmd: Command) -> list[str]:
        return [*ssh_base(), "--", self.host, self.remote_string(cmd)]

    def popen(
        self, cmd: Command, stdin: int | None = None, stdout: int | None = subprocess.PIPE
    ) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            self.argv_for(cmd), stdin=stdin, stdout=stdout, stderr=subprocess.PIPE
        )

    def rsync_shell(self) -> str:
        """The -e value for rsync."""
        return shlex.join(ssh_base())

    # ---- probing -----------------------------------------------------
    @cached_property
    def info(self) -> HostInfo:
        res = run(self, Command(shell=PROBE), timeout=60)
        if not res.ok and not res.stdout:
            raise EichhornError(f"cannot connect to {self.host}: {res.err_text()}")
        values: dict[str, str] = {}
        for line in res.stdout.decode("utf-8", "replace").splitlines():
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
        return HostInfo(
            gnu_find=values.get("printf") == "yes",
            python3=bool(values.get("python3")),
            tar=bool(values.get("tar")),
            rsync_version=parse_rsync_version(values.get("rsync", "")),
            system=values.get("uname", "?"),
            user=values.get("user", "?"),
            paths={k[5:]: v for k, v in values.items() if k.startswith("path_") and v},
        )

    @property
    def capabilities(self) -> ConnectionCapabilities:
        info = self.info
        return ConnectionCapabilities(
            shell=True, sudo=True, rsync=info.rsync_ok, posix_metadata=True, bulk=True
        )

    # ---- files -------------------------------------------------------
    def exists(self, root: str) -> bool:
        return run(self, Command(argv=["test", "-d", root], sudo=self.sudo), timeout=60).ok

    def listing(self, root: str, excludes: Any) -> ListResult:
        info = self.info
        if info.gnu_find:
            cmd = Command(
                argv=["find", root, "-mindepth", "1", "-printf", FIND_FORMAT], sudo=self.sudo
            )
        elif info.python3:
            cmd = Command(argv=["python3", "-c", PY_LISTER, root], sudo=self.sudo)
        else:
            raise EichhornError(
                f"{self.host}: listing files needs GNU find (with -printf) or python3"
            )
        proc = self.popen(cmd, stdin=subprocess.DEVNULL)
        errors = StderrCollector(proc.stderr)
        assert proc.stdout is not None
        data = proc.stdout.read()
        code = proc.wait()
        result = ListResult()
        if code != 0:
            text = errors.text()
            if not data:
                raise_for(text, f"listing {root} on {self.host}")
            result.warnings += [f"listing: {line}" for line in text.splitlines()[:20]]
        fields = data.split(b"\0")
        entries: list[SourceEntry] = []
        for i in range(0, len(fields) - 7, 8):
            t, size, mtime, mode, uid, gid, link, rel = fields[i : i + 8]
            kind = TYPES.get(t.decode())
            rel_text = os.fsdecode(rel)
            if kind is None:
                result.warnings.append(f"skipped {rel_text}: not a regular file, folder or link")
                continue
            entries.append(
                SourceEntry(
                    rel=rel_text,
                    type=kind,
                    size=int(size) if kind == "file" else 0,
                    mtime_ns=int(Decimal(mtime.decode()) * 1_000_000_000),
                    mode=int(mode, 8),
                    uid=int(uid),
                    gid=int(gid),
                    target=os.fsdecode(link) if kind == "symlink" else None,
                )
            )
        result.entries = filter_excluded(entries, excludes)
        return result

    def open_read(self, root: str, rel: str, offset: int = 0) -> Readable:
        path = f"{root.rstrip('/')}/{rel}"
        argv = ["tail", "-c", f"+{offset + 1}", "--", path] if offset else ["cat", "--", path]
        proc = self.popen(Command(argv=argv, sudo=self.sudo), stdin=subprocess.DEVNULL)
        return ProcessReader(proc, f"reading {path} on {self.host}")

    def read_many(self, root: str, rels: list[str]) -> Iterator[Fetched]:
        """Stream many files in one tar archive over a single SSH command."""
        if not rels:
            return
        argv = ["tar", "-C", root, "--no-recursion", "--null", "-T", "-", "-cf", "-"]
        proc = self.popen(Command(argv=argv, sudo=self.sudo), stdin=subprocess.PIPE)
        errors = StderrCollector(proc.stderr)

        def feed() -> None:
            assert proc.stdin is not None
            try:
                for rel in rels:
                    proc.stdin.write(os.fsencode(rel) + b"\0")
                proc.stdin.close()
            except BrokenPipeError:
                pass

        writer = threading.Thread(target=feed, daemon=True)
        writer.start()
        assert proc.stdout is not None
        try:
            with tarfile.open(fileobj=proc.stdout, mode="r|", errors="surrogateescape") as tf:
                for member in tf:
                    name = member.name.removeprefix("./")
                    if member.isfile():
                        yield Fetched(name, tf.extractfile(member), member.size)
                    elif member.islnk():
                        yield Fetched(name, None, 0, link_to=member.linkname.removeprefix("./"))
        except tarfile.ReadError as exc:
            if proc.wait() != 0:
                raise_for(errors.text(), f"tar on {self.host}")
            raise OSError(f"tar stream from {self.host} is broken: {exc}") from exc
        finally:
            proc.stdout.close()
            writer.join(timeout=5)
            proc.wait()
        self.last_tar_errors = errors.text()


__all__ = ["HostInfo", "SshConnection", "ssh_base"]

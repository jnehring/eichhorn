"""Database dumps (spec 5.1 step 3): streamed from the host, compressed locally."""

from __future__ import annotations

import gzip
import hashlib
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eichhorn.connections.base import Command, Connection, StderrCollector
from eichhorn.engines.common import CHUNK
from eichhorn.index import Entry


@dataclass
class DumpResult:
    name: str
    file: str | None
    status: str  # ok | failed
    bytes: int = 0  # compressed, on the drive
    raw_bytes: int = 0  # as produced by the database
    duration_s: float = 0.0
    error: str | None = None
    sha256: str | None = None

    def manifest(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "file": self.file,
            "status": self.status,
            "bytes": self.bytes,
            "raw_bytes": self.raw_bytes,
            "duration_s": round(self.duration_s, 2),
            **({"error": self.error} if self.error else {}),
        }


def file_name(dump: dict[str, Any]) -> str:
    ext = "sql" if dump["type"] in ("postgres", "mysql") else dump.get("ext", "dump")
    return f"{dump['name']}.{ext}.gz"


def _in_container(dump: dict[str, Any], argv: list[str], env_prefix: str = "") -> list[str]:
    """Run argv inside the dump's container (if any); env_prefix needs a shell."""
    container = dump.get("container")
    if env_prefix:
        script = env_prefix + shlex.join(argv)
        inner = ["sh", "-c", script]
    else:
        inner = argv
    return ["docker", "exec", container, *inner] if container else inner


def build_command(dump: dict[str, Any], sudo: bool = False) -> Command:
    kind = dump["type"]
    user = dump.get("user")
    db = dump.get("database")
    pw = dump.get("password_env")
    if kind == "postgres":
        argv = ["pg_dumpall"] if db == "*" else ["pg_dump"]
        if user:
            argv += ["-U", user]
        argv += ["--clean", "--if-exists"]
        if db and db != "*":
            argv.append(db)
        prefix = f'PGPASSWORD="${pw}" ' if pw else ""
        return Command(argv=_in_container(dump, argv, prefix), sudo=sudo)
    if kind == "mysql":
        argv = ["mysqldump"]
        if user:
            argv += ["-u", user]
        argv += ["--single-transaction", "--routines", "--triggers"]
        argv += ["--all-databases"] if db in (None, "*") else ["--databases", db]
        prefix = f'MYSQL_PWD="${pw}" ' if pw else ""
        return Command(argv=_in_container(dump, argv, prefix), sudo=sudo)
    run = dump.get("run") or ""
    if dump.get("container"):
        return Command(argv=["docker", "exec", dump["container"], "sh", "-c", run], sudo=sudo)
    return Command(shell=run, sudo=sudo)


class _HashingWriter:
    def __init__(self, fh: Any) -> None:
        self.fh = fh
        self.h = hashlib.sha256()
        self.size = 0

    def write(self, data: bytes) -> int:
        self.h.update(data)
        self.size += len(data)
        written: int = self.fh.write(data)
        return written

    def flush(self) -> None:
        self.fh.flush()


def run_dump(conn: Connection, dump: dict[str, Any], dumps_dir: Path, sudo: bool) -> DumpResult:
    name = file_name(dump)
    target = dumps_dir / name
    partial = target.with_name(name + ".partial")
    started = time.monotonic()
    cmd = build_command(dump, sudo)
    result = DumpResult(dump["name"], f"dumps/{name}", "failed")
    proc = conn.popen(cmd, stdin=subprocess.DEVNULL)
    errors = StderrCollector(proc.stderr)
    assert proc.stdout is not None
    raw = 0
    with open(partial, "wb") as fh:
        writer = _HashingWriter(fh)
        with gzip.GzipFile(filename="", mode="wb", fileobj=writer, mtime=0) as gz:
            while chunk := proc.stdout.read(CHUNK):
                raw += len(chunk)
                gz.write(chunk)
        fh.flush()
        os.fsync(fh.fileno())
    code = proc.wait()
    result.duration_s = time.monotonic() - started
    result.raw_bytes = raw
    if code != 0 or raw == 0:
        partial.unlink(missing_ok=True)
        text = errors.text()[-1500:] or ("empty output" if raw == 0 else f"exit {code}")
        result.error = f"{cmd.describe()}: {text}"
        result.file = None
        return result
    os.replace(partial, target)
    result.status = "ok"
    result.bytes = writer.size
    result.sha256 = writer.h.hexdigest()
    return result


def index_entry(result: DumpResult) -> Entry | None:
    if result.status != "ok" or not result.file:
        return None
    return Entry(p=result.file, type="file", size=result.bytes, sha256=result.sha256)

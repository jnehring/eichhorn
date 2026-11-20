"""Database dumps (spec 5.1 step 3): streamed from the host, compressed locally."""

from __future__ import annotations

import gzip
import hashlib
import os
import shlex
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
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
    bytes: int = 0  # on the drive (compressed; for delta dumps: the patch, or the full dump)
    raw_bytes: int = 0  # as produced by the database
    duration_s: float = 0.0
    error: str | None = None
    sha256: str | None = None
    entries: list[Entry] = field(default_factory=list)  # index entries to add
    delta: dict[str, Any] | None = None

    def manifest(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "file": self.file,
            "status": self.status,
            "bytes": self.bytes,
            "raw_bytes": self.raw_bytes,
            "duration_s": round(self.duration_s, 2),
        }
        if self.error:
            out["error"] = self.error
        if self.delta:
            out["delta"] = self.delta
        return out


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


def _mysql_tool(dump: dict[str, Any], new: str, old: str, args: list[str], sudo: bool) -> Command:
    """Run MariaDB's tool, or MySQL's where MariaDB's is missing.

    MariaDB 11 and newer have only `mariadb-dump`/`mariadb`; MySQL has only
    `mysqldump`/`mysql`. The password comes from an environment variable.
    """
    pw = dump.get("password_env")
    script = (
        (f'MYSQL_PWD="${pw}"; export MYSQL_PWD; ' if pw else "")
        + f"T=$(command -v {new} || command -v {old}) || "
        + f"{{ echo 'neither {new} nor {old} found' >&2; exit 127; }}; "
        + 'exec "$T" '
        + shlex.join(args)
    )
    container = dump.get("container")
    if container:
        return Command(argv=["docker", "exec", container, "sh", "-c", script], sudo=sudo)
    return Command(shell=script, sudo=sudo)


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
        args: list[str] = []
        if user:
            args += ["-u", user]
        args += ["--single-transaction", "--routines", "--triggers"]
        args += ["--all-databases"] if db in (None, "*") else ["--databases", db]
        return _mysql_tool(dump, "mariadb-dump", "mysqldump", args, sudo)
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


def _stream(
    conn: Connection, cmd: Command, out_path: Path, compress: bool
) -> tuple[int, int, str, str, int]:
    """Run the dump command; write gzip (compress) or raw output. Returns
    (exit code, raw bytes, raw sha256, stored sha256, stored bytes)."""
    proc = conn.popen(cmd, stdin=subprocess.DEVNULL)
    errors = StderrCollector(proc.stderr)
    assert proc.stdout is not None
    raw = 0
    raw_hash = hashlib.sha256()
    with open(out_path, "wb") as fh:
        writer = _HashingWriter(fh)
        if compress:
            with gzip.GzipFile(filename="", mode="wb", fileobj=writer, mtime=0) as gz:
                while chunk := proc.stdout.read(CHUNK):
                    raw += len(chunk)
                    raw_hash.update(chunk)
                    gz.write(chunk)
        else:
            while chunk := proc.stdout.read(CHUNK):
                raw += len(chunk)
                raw_hash.update(chunk)
                writer.write(chunk)
        fh.flush()
        os.fsync(fh.fileno())
    code = proc.wait()
    _run_dump_errors[out_path] = errors.text()
    return code, raw, raw_hash.hexdigest(), writer.h.hexdigest(), writer.size


_run_dump_errors: dict[Path, str] = {}


def run_dump(
    conn: Connection,
    dump: dict[str, Any],
    dumps_dir: Path,
    sudo: bool,
    snapshot: str = "",
    hardlinks: bool = True,
    base: DeltaBase | None = None,
) -> DumpResult:
    if dump.get("delta"):
        return _run_delta_dump(conn, dump, dumps_dir, sudo, snapshot, hardlinks, base)
    name = file_name(dump)
    target = dumps_dir / name
    partial = target.with_name(name + ".partial")
    started = time.monotonic()
    cmd = build_command(dump, sudo)
    result = DumpResult(dump["name"], f"dumps/{name}", "failed")
    code, raw, _raw_sha, sha, size = _stream(conn, cmd, partial, compress=True)
    result.duration_s = time.monotonic() - started
    result.raw_bytes = raw
    if code != 0 or raw == 0:
        partial.unlink(missing_ok=True)
        text = _run_dump_errors.pop(partial, "")[-1500:] or (
            "empty output" if raw == 0 else f"exit {code}"
        )
        result.error = f"{cmd.describe()}: {text}"
        result.file = None
        return result
    os.replace(partial, target)
    result.status = "ok"
    result.bytes = size
    result.sha256 = sha
    result.entries = [Entry(p=result.file or "", type="file", size=size, sha256=sha)]
    return result


def index_entry(result: DumpResult) -> Entry | None:
    """The main index entry of a dump (kept for callers of the first version)."""
    return result.entries[0] if result.status == "ok" and result.entries else None


# ---------------------------------------------------------------- delta dumps (spec 5.8)
DELTA_LIMIT = 2 * 1024**3 - 1  # zstd's window caps the reference at about 2 GiB
ZSTD_LEVEL = 10


@dataclass
class DeltaBase:
    """The full dump of an earlier snapshot that a patch can refer to."""

    snapshot: str  # where the full dump was made
    paths: list[Path]  # where its .zst content is (on the drive)
    entry: Entry  # its index entry (path and hash)
    runs_since_full: int
    raw_bytes: int


def delta_names(dump: dict[str, Any]) -> tuple[str, str]:
    ext = "sql" if dump["type"] in ("postgres", "mysql") else dump.get("ext", "dump")
    return f"{dump['name']}.base.{ext}.zst", f"{dump['name']}.patch.zst"


def _settings(dump: dict[str, Any]) -> tuple[int, float, float]:
    from eichhorn.schema import parse_duration

    d = dump.get("delta") or {}
    return (
        int(d.get("full_every", 10)),
        float(d.get("max_ratio", 0.5)),
        parse_duration(d.get("max_base_age", "90d")),
    )


def _window_log(size: int) -> int:
    import math

    return min(31, max(10, math.ceil(math.log2(size + 1))))


def _read_zst(paths: list[Path]) -> bytes:
    import zstandard

    data = b"".join(p.read_bytes() for p in paths)
    return zstandard.ZstdDecompressor(max_window_size=2**31).decompressobj().decompress(data)


def make_patch(base_raw: bytes, new_raw: bytes) -> bytes:
    import zstandard

    d = zstandard.ZstdCompressionDict(base_raw, dict_type=zstandard.DICT_TYPE_RAWCONTENT)
    params = zstandard.ZstdCompressionParameters.from_level(
        ZSTD_LEVEL, window_log=_window_log(len(base_raw) + len(new_raw)), enable_ldm=True
    )
    return zstandard.ZstdCompressor(dict_data=d, compression_params=params).compress(new_raw)


def apply_patch(base_raw: bytes, patch: bytes) -> bytes:
    import zstandard

    d = zstandard.ZstdCompressionDict(base_raw, dict_type=zstandard.DICT_TYPE_RAWCONTENT)
    return zstandard.ZstdDecompressor(dict_data=d, max_window_size=2**31).decompress(patch)


def _compress_file(raw_path: Path, out_path: Path) -> tuple[str, int]:
    import zstandard

    size = raw_path.stat().st_size
    params = zstandard.ZstdCompressionParameters.from_level(
        ZSTD_LEVEL, window_log=_window_log(size), enable_ldm=True
    )
    cctx = zstandard.ZstdCompressor(compression_params=params)
    with open(raw_path, "rb") as src, open(out_path, "wb") as fh:
        writer = _HashingWriter(fh)
        cctx.copy_stream(src, writer, size=size)  # type: ignore[arg-type]
        fh.flush()
        os.fsync(fh.fileno())
    return writer.h.hexdigest(), writer.size


def _run_delta_dump(
    conn: Connection,
    dump: dict[str, Any],
    dumps_dir: Path,
    sudo: bool,
    snapshot: str,
    hardlinks: bool,
    base: DeltaBase | None,
) -> DumpResult:
    from datetime import datetime

    started = time.monotonic()
    base_name, patch_name = delta_names(dump)
    cmd = build_command(dump, sudo)
    result = DumpResult(dump["name"], None, "failed")
    raw_path = dumps_dir / f"{dump['name']}.raw.partial"
    code, raw, raw_sha, _, _ = _stream(conn, cmd, raw_path, compress=False)
    result.raw_bytes = raw
    try:
        if code != 0 or raw == 0:
            text = _run_dump_errors.pop(raw_path, "")[-1500:] or (
                "empty output" if raw == 0 else f"exit {code}"
            )
            result.error = f"{cmd.describe()}: {text}"
            return result
        full_every, max_ratio, max_age = _settings(dump)
        reason = ""
        if base is None:
            reason = "no earlier full dump"
        elif base.runs_since_full + 1 >= full_every:
            reason = f"full_every {full_every}"
        elif raw > DELTA_LIMIT or base.raw_bytes > DELTA_LIMIT:
            reason = "dump larger than 2 GiB"
        else:
            base_time = datetime.strptime(base.snapshot, "%Y-%m-%dT%H-%M-%S")
            if (datetime.now() - base_time).total_seconds() > max_age:
                reason = "full dump older than max_base_age"
        full_path = dumps_dir / base_name
        if not reason and base is not None:
            base_raw = _read_zst(base.paths)
            new_raw = raw_path.read_bytes()
            patch = make_patch(base_raw, new_raw)
            full_sha, full_size = _compress_file(raw_path, full_path.with_name(base_name + ".tmp"))
            if len(patch) > max_ratio * full_size:
                reason = f"patch over {max_ratio:.0%} of a full dump"
                os.replace(full_path.with_name(base_name + ".tmp"), full_path)
            else:
                full_path.with_name(base_name + ".tmp").unlink()
                patch_path = dumps_dir / patch_name
                patch_path.write_bytes(patch)
                patch_sha = hashlib.sha256(patch).hexdigest()
                base_entry = Entry(
                    p=f"dumps/{base_name}",
                    type="file",
                    size=base.entry.size,
                    sha256=base.entry.sha256,
                    here=False,
                )
                if hardlinks:
                    os.link(base.paths[0], full_path)
                    base_entry.here = True
                result.entries = [
                    base_entry,
                    Entry(p=f"dumps/{patch_name}", type="file", size=len(patch), sha256=patch_sha),
                ]
                result.file = f"dumps/{patch_name}"
                result.bytes = len(patch)
                result.sha256 = patch_sha
                result.delta = {
                    "mode": "patch",
                    "base_file": f"dumps/{base_name}",
                    "patch_file": f"dumps/{patch_name}",
                    "base_snapshot": base.snapshot,
                    "runs_since_full": base.runs_since_full + 1,
                    "base_raw_bytes": base.raw_bytes,
                    "rebuilt_sha256": raw_sha,
                    "full_bytes_estimate": full_size,
                }
        if reason:
            if not full_path.exists():
                full_sha, full_size = _compress_file(raw_path, full_path)
            else:
                full_sha = hashlib.sha256(full_path.read_bytes()).hexdigest()
                full_size = full_path.stat().st_size
            result.entries = [
                Entry(p=f"dumps/{base_name}", type="file", size=full_size, sha256=full_sha)
            ]
            result.file = f"dumps/{base_name}"
            result.bytes = full_size
            result.sha256 = full_sha
            result.delta = {
                "mode": "full",
                "base_file": f"dumps/{base_name}",
                "base_snapshot": snapshot,
                "runs_since_full": 0,
                "base_raw_bytes": raw,
                "rebuilt_sha256": raw_sha,
                "reason": reason,
            }
        result.status = "ok"
        return result
    finally:
        raw_path.unlink(missing_ok=True)
        result.duration_s = time.monotonic() - started


def rebuild(resolver: Any, snapshot: str, dump: dict[str, Any]) -> Iterator[bytes]:
    """The original (uncompressed) dump of a snapshot, as a stream of chunks."""
    import zlib

    import zstandard

    index = resolver.index(snapshot)
    delta = dump.get("delta")
    if not delta:
        entry = index[dump["file"]]
        z = zlib.decompressobj(wbits=31)
        for chunk in resolver.chunks(snapshot, entry):
            yield z.decompress(chunk)
        yield z.flush()
        return
    base_entry = index[delta["base_file"]]
    if delta["mode"] == "full":
        d = zstandard.ZstdDecompressor(max_window_size=2**31).decompressobj()
        for chunk in resolver.chunks(snapshot, base_entry):
            yield d.decompress(chunk)
        return
    base_raw = (
        zstandard.ZstdDecompressor(max_window_size=2**31)
        .decompressobj()
        .decompress(b"".join(resolver.chunks(snapshot, base_entry)))
    )
    patch = b"".join(resolver.chunks(snapshot, index[delta["patch_file"]]))
    yield apply_patch(base_raw, patch)

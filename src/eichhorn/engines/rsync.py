"""The rsync engine (spec 5.2.1): delta transfer, for POSIX repositories.

The host is listed first (for the index and the counts, exactly as in the
builtin engine). rsync then does the transfer with --link-dest. Afterwards
each file is matched with the previous snapshot by inode: a hardlink keeps its
known hash, anything else is new data and is hashed once.
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Any

from eichhorn import progress
from eichhorn.config import Source
from eichhorn.connections.base import Connection, StderrCollector
from eichhorn.connections.ssh import SshConnection
from eichhorn.engines.common import Previous, SourceStats, entry_for, hash_file, parent
from eichhorn.errors import EichhornError
from eichhorn.excludes import Excludes
from eichhorn.index import Entry
from eichhorn.stores.base import StoreCapabilities
from eichhorn.tools import local_rsync

RECEIVED_RE = re.compile(r"Total bytes received:\s*([\d,.]+)")
SENT_RE = re.compile(r"Total bytes sent:\s*([\d,.]+)")


class RsyncEngine:
    name = "rsync"

    def __init__(self, caps: StoreCapabilities) -> None:
        self.caps = caps
        found = local_rsync()
        if found is None:
            raise EichhornError("the rsync engine needs rsync 3.1 or newer on this computer")
        self.rsync = found[0]

    def command(self, conn: Connection, source: Source, dest: Path, prev: Previous) -> list[str]:
        argv = [
            self.rsync,
            "--archive",
            "--hard-links",
            "--numeric-ids",
            "--delete",
            "--partial",
            "--protect-args",
            "--stats",
            "--info=progress2",
        ]
        argv += [f"--exclude={p}" for p in source.exclude]
        if prev.dir is not None and (prev.dir / "files" / source.name).is_dir():
            argv.append(f"--link-dest={(prev.dir / 'files' / source.name).absolute()}/")
        src = source.path.rstrip("/") + "/"
        if isinstance(conn, SshConnection):
            argv += ["-e", conn.rsync_shell()]
            if conn.sudo:
                argv.append("--rsync-path=sudo -n rsync")
            src = f"{conn.host}:{src}"
        return [*argv, src, f"{dest}/"]

    def transfer(
        self, conn: Connection, source: Source, dest: Path, prev: Previous
    ) -> tuple[list[Entry], SourceStats]:
        started = time.monotonic()
        stats = SourceStats(source.name, source.path)
        listing = conn.listing(source.path, Excludes(source.exclude))
        stats.warnings += listing.warnings
        prefix = f"files/{source.name}"
        if not self.caps.case_sensitive:
            seen_lower: dict[str, str] = {}
            for e in listing.entries:
                other = seen_lower.setdefault(e.rel.lower(), e.rel)
                if other != e.rel:
                    raise EichhornError(
                        f"source {source.name!r}: {other!r} and {e.rel!r} differ only in case, "
                        "and this repository is case-insensitive. Use engine: builtin for this "
                        "target."
                    )
        target_dir = dest / prefix
        target_dir.mkdir(parents=True, exist_ok=True)
        argv = self.command(conn, source, target_dir, prev)
        to_copy = files_to_copy = 0
        for e in listing.entries:
            old = prev.index.get(f"{prefix}/{e.rel}")
            if e.type == "file" and (
                old is None or (old.size, old.mtime_ns) != (e.size, e.mtime_ns)
            ):
                to_copy += e.size
                files_to_copy += 1
        prog = progress.current()
        prog.phase(
            "transfer",
            source.name,
            part=prog.part,
            bytes_total=to_copy,
            files_total=files_to_copy,
        )
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        errors = StderrCollector(proc.stderr)
        assert proc.stdout is not None
        out = _read_with_progress(proc.stdout, prog)
        code = proc.wait()
        if code == 24:
            stats.warnings.append("some files vanished during the transfer (rsync exit 24)")
        elif code != 0:
            raise EichhornError(f"rsync failed for {source.name!r} (exit {code}): {errors.text()}")
        # pulling from a host, the data is "received"; copying locally, it is "sent"
        numbers = [
            int(re.sub(r"[,.]", "", m.group(1)))
            for m in (RECEIVED_RE.search(out), SENT_RE.search(out))
            if m
        ]
        received = max(numbers, default=0)

        entries: list[Entry] = [Entry(prefix, "dir")]
        seen: set[str] = set()
        prev_files = prev.dir / prefix if prev.dir is not None else None
        for item in listing.entries:
            p = f"{prefix}/{item.rel}"
            entry = entry_for(item, p, None)
            if item.type == "dir":
                stats.dirs += 1
                entries.append(entry)
                seen.add(p)
                continue
            if item.type == "symlink":
                entries.append(entry)
                seen.add(p)
                continue
            path = target_dir / item.rel
            try:
                st = path.stat()
            except FileNotFoundError:
                stats.warnings.append(f"vanished during the transfer: {item.rel}")
                continue
            seen.add(p)
            entry.size = st.st_size
            old = prev.index.get(p)
            linked = False
            if old is not None and old.sha256 and prev_files is not None:
                try:
                    linked = (prev_files / item.rel).stat().st_ino == st.st_ino
                except OSError:
                    linked = False
            if linked and old is not None:
                entry.sha256 = old.sha256
                stats.unchanged += 1
            else:
                entry.sha256 = hash_file(path)
                is_new = old is None or old.type != "file"
                stats.count_fetched(entry, is_new, parent(item.rel), 0)
            stats.files += 1
            stats.bytes += entry.size
            entries.append(entry)
        stats.transferred_bytes = received
        stats.count_deleted(prev.index, prefix, seen)
        stats.duration_s = time.monotonic() - started
        return entries, stats


PROGRESS_RE = re.compile(rb"^\s*([\d,.]+)\s+\d+%")


def _read_with_progress(stream: Any, p: progress.Progress) -> str:
    """Read rsync's output; feed --info=progress2 lines (ended by \\r) to the progress."""
    text = bytearray()
    buf = b""
    while chunk := stream.read1(65536) if hasattr(stream, "read1") else stream.read(65536):
        text += chunk
        buf += chunk
        *lines, buf = re.split(rb"[\r\n]", buf)
        for line in lines:
            m = PROGRESS_RE.match(line)
            if m:
                p.set_done(int(re.sub(rb"[,.]", b"", m.group(1))))
    return text.decode("utf-8", "replace")

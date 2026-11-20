"""Detect what a repository's file system can do (spec 3.1)."""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

FAT32_MAX = 4 * 1024**3 - 1
NO_POSIX = {"ntfs", "ntfs3", "fuseblk", "exfat", "msdos", "vfat", "fat32", "fat"}
FAT32_NAMES = {"msdos", "vfat", "fat32", "fat"}


@dataclass
class FsInfo:
    name: str  # best effort, lower case, e.g. apfs, ext4, ntfs, exfat, msdos
    hardlinks: bool
    case_sensitive: bool
    posix_metadata: bool
    max_file_size: int | None

    @property
    def storage(self) -> str:
        return "hardlinks" if self.hardlinks else "sparse"


def _fs_name(path: Path) -> str:
    try:
        if sys.platform == "win32":
            import ctypes

            root = os.path.splitdrive(str(path.resolve()))[0] + "\\"
            buf = ctypes.create_unicode_buffer(64)
            ok = ctypes.windll.kernel32.GetVolumeInformationW(  # type: ignore[attr-defined]
                ctypes.c_wchar_p(root), None, 0, None, None, None, buf, len(buf)
            )
            return buf.value.lower() if ok else "unknown"
        if sys.platform == "darwin":
            out = subprocess.run(["/bin/df", str(path)], capture_output=True, text=True).stdout
            device = out.splitlines()[-1].split()[0]
            mounts = subprocess.run(["/sbin/mount"], capture_output=True, text=True).stdout
            for line in mounts.splitlines():
                if line.startswith(device + " on "):
                    return line.rsplit("(", 1)[1].split(",")[0].strip().lower()
            return "unknown"
        best, name = "", "unknown"
        resolved = str(path.resolve())
        with open("/proc/mounts", encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                mount, fstype = parts[1].replace("\\040", " "), parts[2]
                if (resolved == mount or resolved.startswith(mount.rstrip("/") + "/")) and len(
                    mount
                ) > len(best):
                    best, name = mount, fstype.lower()
        return name
    except (OSError, IndexError, subprocess.SubprocessError):
        return "unknown"


def probe(directory: Path) -> FsInfo:
    """Test hardlinks and case sensitivity by creating small files in `directory`."""
    directory.mkdir(parents=True, exist_ok=True)
    tag = uuid.uuid4().hex[:8]
    a = directory / f".eichhorn-probe-{tag}-a"
    b = directory / f".eichhorn-probe-{tag}-b"
    upper = directory / f".EICHHORN-PROBE-{tag}-A"
    hardlinks = False
    try:
        a.write_bytes(b"probe")
        try:
            os.link(a, b)
            hardlinks = True
        except OSError:
            hardlinks = False
        case_sensitive = not upper.exists()
    finally:
        for p in (a, b):
            with contextlib.suppress(FileNotFoundError):
                p.unlink()
    name = _fs_name(directory)
    posix = sys.platform != "win32" and name not in NO_POSIX
    max_size = FAT32_MAX if name in FAT32_NAMES else None
    return FsInfo(name, hardlinks, case_sensitive, posix, max_size)

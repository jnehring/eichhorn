"""Finding external tools and their versions."""

from __future__ import annotations

import functools
import re
import shutil
import subprocess

RSYNC_MIN = (3, 1)


def parse_rsync_version(text: str) -> tuple[int, int, int] | None:
    m = re.search(r"rsync\s+version\s+v?(\d+)\.(\d+)\.(\d+)", text)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


@functools.cache
def local_rsync() -> tuple[str, tuple[int, int, int]] | None:
    """Path and version of a usable local rsync (>= 3.1), or None."""
    path = shutil.which("rsync")
    if not path:
        return None
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    version = parse_rsync_version(out.stdout)
    if version is None or version[:2] < RSYNC_MIN:
        return None
    return path, version


def local_rsync_note() -> str:
    path = shutil.which("rsync")
    if not path:
        return "rsync is not installed"
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return "rsync does not start"
    v = parse_rsync_version(out.stdout)
    if v is None:
        return f"{path}: unknown version (openrsync?)"
    if v[:2] < RSYNC_MIN:
        return f"{path} is version {'.'.join(map(str, v))}; 3.1 or newer is needed"
    return f"{path} {'.'.join(map(str, v))}"

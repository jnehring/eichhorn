"""Platform folders, volume discovery and Windows path helpers."""

from __future__ import annotations

import contextlib
import os
import string
import sys
from pathlib import Path

import platformdirs

APP = "eichhorn"
REPO_DIRNAME = "eichhorn"  # discovery looks for <volume>/eichhorn/repository.yaml


def config_dir() -> Path:
    env = os.environ.get("EICHHORN_CONFIG")
    if env:
        return Path(env).expanduser()
    return Path(platformdirs.user_config_dir(APP, appauthor=False, roaming=True))


def state_dir() -> Path:
    env = os.environ.get("EICHHORN_STATE")
    if env:
        return Path(env).expanduser()
    state = Path(platformdirs.user_state_dir(APP, appauthor=False))
    if state == config_dir():  # macOS puts both in Application Support
        state = state / "state"
    return state


def volume_roots() -> list[Path]:
    """Mounted volumes where a repository may live (spec 3.6)."""
    roots: list[Path] = []
    if sys.platform == "win32":
        import ctypes

        bitmask = ctypes.windll.kernel32.GetLogicalDrives()  # type: ignore[attr-defined]
        for i, letter in enumerate(string.ascii_uppercase):
            if bitmask & (1 << i):
                root = Path(f"{letter}:\\")
                try:
                    if root.exists():
                        roots.append(root)
                except OSError:  # drive not ready (empty card reader, …)
                    pass
        return roots
    candidates: list[Path] = []
    if sys.platform == "darwin":
        candidates.append(Path("/Volumes"))
    else:
        user = os.environ.get("USER") or ""
        candidates += [Path("/media") / user, Path("/run/media") / user, Path("/mnt")]
    for base in candidates:
        with contextlib.suppress(OSError):
            roots += [p for p in base.iterdir() if p.is_dir()]
    return roots


def long_path(path: Path) -> Path:
    r"""On Windows, use the \\?\ prefix so paths over 260 characters work."""
    if sys.platform != "win32":
        return path
    text = str(path.resolve())
    if text.startswith("\\\\?\\"):
        return path
    if text.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + text[2:])
    return Path("\\\\?\\" + text)

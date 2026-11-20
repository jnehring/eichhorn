"""Eichhorn: plain-file, hardlinked snapshot backups onto local drives."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("eichhorn")
except PackageNotFoundError:  # running from a source tree without installation
    __version__ = "0.0.0"

__all__ = ["__version__"]

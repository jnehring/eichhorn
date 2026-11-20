"""Storing source file names on any file system (spec 5.4)."""

from __future__ import annotations

import re
import sys

INVALID_CHARS = set('<>:"\\|?*') | {chr(c) for c in range(32)}
RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}  # fmt: skip


def needs_escaping(name: str, windows_rules: bool) -> bool:
    if not windows_rules:
        return "\x00" in name
    if any(c in INVALID_CHARS for c in name):
        return True
    if name.endswith((".", " ")):
        return True
    return name.split(".")[0].upper() in RESERVED


def escape(name: str, windows_rules: bool) -> str:
    """Escape a single path component so the repository's file system can store it."""
    if not needs_escaping(name, windows_rules):
        return name
    out = []
    for c in name:
        if c in INVALID_CHARS or c == "%":
            out.append(f"%{ord(c):02X}")
        else:
            out.append(c)
    text = "".join(out)
    if text.endswith((".", " ")):
        text = text[:-1] + f"%{ord(text[-1]):02X}"
    if text.split(".")[0].upper() in RESERVED:
        text = "%" + f"{ord(text[0]):02X}" + text[1:]
    return text


_SUFFIX_RE = re.compile(r"^(.*?)(\.[^./]*)?$")


def collision_name(name: str, n: int) -> str:
    """`photo.jpg` → `photo (eichhorn-2).jpg`."""
    m = _SUFFIX_RE.match(name)
    stem, ext = (m.group(1), m.group(2) or "") if m else (name, "")
    if not stem:  # dotfile such as .env
        return f"{name} (eichhorn-{n})"
    return f"{stem} (eichhorn-{n}){ext}"


def windows_rules_for(posix_metadata: bool) -> bool:
    """Use NTFS/FAT naming rules unless the repository is a POSIX file system on POSIX."""
    return sys.platform == "win32" or not posix_metadata


class NameMapper:
    """Maps the components of one folder, handling escapes and case collisions."""

    def __init__(self, windows_rules: bool, case_sensitive: bool) -> None:
        self.windows_rules = windows_rules
        self.case_sensitive = case_sensitive
        self._used: dict[str, set[str]] = {}

    def stored_name(self, parent_key: str, name: str) -> str:
        stored = escape(name, self.windows_rules)
        if self.case_sensitive:
            return stored
        used = self._used.setdefault(parent_key, set())
        candidate, n = stored, 1
        while candidate.lower() in used:
            n += 1
            candidate = collision_name(stored, n)
        used.add(candidate.lower())
        return candidate

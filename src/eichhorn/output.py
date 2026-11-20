"""Small helpers for human-readable terminal output."""

from __future__ import annotations

import os
import sys
from datetime import datetime


def use_color() -> bool:
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if use_color() else text


def bold(t: str) -> str:
    return _c("1", t)


def dim(t: str) -> str:
    return _c("2", t)


def green(t: str) -> str:
    return _c("32", t)


def yellow(t: str) -> str:
    return _c("33", t)


def red(t: str) -> str:
    return _c("31", t)


def status_color(status: str) -> str:
    return {"ok": green, "partial": yellow}.get(status, red)(status)


def size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} TB"


def duration(seconds: float) -> str:
    s = round(seconds)
    if s < 60:
        return f"{seconds:.1f} s" if seconds < 10 else f"{s} s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m} min {s} s" if s else f"{m} min"
    h, m = divmod(m, 60)
    return f"{h} h {m} min"


def age(iso: str) -> str:
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return "?"
    days = (datetime.now().astimezone() - then).total_seconds() / 86400
    if days < 1 / 24:
        return "just now"
    if days < 1:
        return f"{int(days * 24)} h ago"
    return f"{int(days)} days ago" if days >= 2 else "1 day ago"


def table(rows: list[list[str]], header: list[str]) -> str:
    import re

    strip = re.compile(r"\033\[[0-9;]*m")

    def width(s: str) -> int:
        return len(strip.sub("", s))

    cols = [max(width(r[i]) for r in [header, *rows]) for i in range(len(header))]
    lines = ["  ".join(bold(h.ljust(cols[i])) for i, h in enumerate(header)).rstrip()]
    for r in rows:
        lines.append("  ".join(c + " " * (cols[i] - width(c)) for i, c in enumerate(r)).rstrip())
    return "\n".join(lines)

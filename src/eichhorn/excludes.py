"""Exclude patterns with one syntax for both engines (spec 4.3).

* matches anything except /     ** matches anything including /
? matches one character          [abc] matches one of a set
a leading / anchors the pattern to the source root
a trailing / matches folders only
Without a leading /, a pattern matches at any depth (like rsync).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


def _translate(pattern: str) -> str:
    out = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if pattern[i : i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            j = pattern.find("]", i + 1)
            if j == -1:
                out.append(re.escape(c))
            else:
                body = pattern[i + 1 : j]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append("[" + body.replace("\\", "\\\\") + "]")
                i = j
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out)


@dataclass(frozen=True)
class _Rule:
    regex: re.Pattern[str]
    dir_only: bool


class Excludes:
    def __init__(self, patterns: list[str]) -> None:
        self.patterns = list(patterns)
        rules = []
        for p in patterns:
            dir_only = p.endswith("/")
            body = p.rstrip("/")
            if body.startswith("/"):
                regex = "^" + _translate(body[1:]) + "$"
            else:
                regex = "^(?:.*/)?" + _translate(body) + "$"
            rules.append(_Rule(re.compile(regex), dir_only))
        self._rules = rules

    def excluded(self, rel_path: str, is_dir: bool) -> bool:
        """`rel_path` is relative to the source root, with / separators."""
        return any(r.regex.match(rel_path) and (is_dir or not r.dir_only) for r in self._rules)

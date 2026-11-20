"""Which snapshots to keep (spec 7)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

RULES = ("keep_last", "keep_daily", "keep_weekly", "keep_monthly", "keep_yearly")


@dataclass(frozen=True)
class SnapInfo:
    name: str
    time: datetime
    status: str
    pinned: bool


def _bucket(rule: str, t: datetime) -> object:
    if rule == "keep_daily":
        return t.date()
    if rule == "keep_weekly":
        iso = t.isocalendar()
        return (iso[0], iso[1])
    if rule == "keep_monthly":
        return (t.year, t.month)
    return t.year


def plan(snaps: list[SnapInfo], rules: dict[str, int]) -> dict[str, list[str]]:
    """Map every snapshot that is kept to the reasons why. Others may be deleted."""
    keep: dict[str, list[str]] = {}
    if not any(rules.get(r) for r in RULES):
        return {s.name: ["no retention rules"] for s in snaps}
    newest_first = sorted(snaps, key=lambda s: s.name, reverse=True)
    for s in newest_first:
        if s.pinned:
            keep.setdefault(s.name, []).append("pinned")
    newest_ok = next((s for s in newest_first if s.status == "ok"), None)
    if newest_ok:
        keep.setdefault(newest_ok.name, []).append("newest ok")
    n = rules.get("keep_last") or 0
    for s in newest_first[:n]:
        keep.setdefault(s.name, []).append("last")
    for rule in RULES[1:]:
        n = rules.get(rule) or 0
        if not n:
            continue
        seen: set[object] = set()
        for s in newest_first:
            b = _bucket(rule, s.time)
            if b in seen:
                continue
            if len(seen) >= n:
                break
            seen.add(b)
            keep.setdefault(s.name, []).append(rule.removeprefix("keep_"))
    return keep

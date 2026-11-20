"""Run summary (spec 5.9)."""

from __future__ import annotations

from typing import Any

from eichhorn import output as o


def changes_line(ch: dict[str, Any]) -> str:
    parts = []
    if ch.get("new"):
        parts.append(o.green(f"+{ch['new']} new"))
    if ch.get("changed"):
        parts.append(o.yellow(f"~{ch['changed']} changed"))
    if ch.get("deleted"):
        parts.append(o.red(f"−{ch['deleted']} deleted"))
    return "   ".join(parts) if parts else o.dim("no changes")


def format_summary(m: dict[str, Any]) -> str:
    total = m.get("timings", {}).get("total", 0)
    head = (
        f"{o.bold(m['target'])} → {m.get('repository_name', '?')}  {m['snapshot']}  "
        f"{o.status_color(m['status'])}  ({o.duration(total)})"
    )
    if m.get("label"):
        head += f"  “{m['label']}”"
    lines = [head]
    width = max([len(s["name"]) for s in m.get("sources", [])] + [10])
    for s in m.get("sources", []):
        ch = s.get("changes", {})
        line = f"  {s['name'].ljust(width)} {changes_line(ch)}"
        if s.get("transferred_bytes"):
            line += f"   {o.size(s['transferred_bytes'])} transferred"
        lines.append(line)
        top = [t for t in ch.get("top_folders", []) if t["changes"] > 1]
        if top:
            lines.append(f"  {' ' * width} most changes: {top[0]['folder']} ({top[0]['changes']})")
    repo = m.get("repository", {})
    stored = sum(s.get("stored_bytes", 0) for s in m.get("sources", []))
    lines.append(
        f"  {'repository'.ljust(width)} +{o.size(stored)} used, "
        f"{o.size(repo.get('free_after', 0))} free"
    )
    for w in m.get("warnings", [])[:10]:
        lines.append(o.yellow(f"  ! {w}"))
    if len(m.get("warnings", [])) > 10:
        lines.append(o.yellow(f"  ! … {len(m['warnings']) - 10} more warnings in the log"))
    return "\n".join(lines)


def totals(m: dict[str, Any]) -> dict[str, int]:
    out = {"files": 0, "bytes": 0, "new": 0, "changed": 0, "deleted": 0, "stored_bytes": 0}
    for s in m.get("sources", []):
        out["files"] += s.get("files", 0)
        out["bytes"] += s.get("bytes", 0)
        out["stored_bytes"] += s.get("stored_bytes", 0)
        for k in ("new", "changed", "deleted"):
            out[k] += s.get("changes", {}).get(k, 0)
    return out

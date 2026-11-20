"""Pins and labels added after a snapshot was made (spec 7.1).

Finished snapshot folders never change, so these live per target in
targets/<target>/annotations.yaml and override the manifest's values.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from eichhorn.stores.localfs import LocalRepository, write_atomic
from eichhorn.yamlload import dump, load_file

FILE = "annotations.yaml"


def load(repo: LocalRepository, target: str) -> dict[str, dict[str, Any]]:
    f = repo.target_dir(target) / FILE
    if not f.is_file():
        return {}
    data = load_file(f).data or {}
    return {str(k): dict(v or {}) for k, v in data.items()}


def save(repo: LocalRepository, target: str, data: dict[str, dict[str, Any]]) -> None:
    clean = {k: v for k, v in sorted(data.items()) if v}
    write_atomic(repo.target_dir(target) / FILE, dump(clean) if clean else "{}\n")


def set_pin(
    repo: LocalRepository, target: str, snapshot: str, pinned: bool, note: str | None = None
) -> None:
    data = load(repo, target)
    item = data.setdefault(snapshot, {})
    item["pinned"] = pinned
    if pinned:
        item["pinned_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        if note:
            item["note"] = note
    else:
        item.pop("pinned_at", None)
        item.pop("note", None)
    save(repo, target, data)


def set_label(repo: LocalRepository, target: str, snapshot: str, text: str) -> None:
    data = load(repo, target)
    item = data.setdefault(snapshot, {})
    item["label"] = text  # an empty text removes a label from the manifest too
    save(repo, target, data)


def effective(
    manifest: dict[str, Any], notes: dict[str, dict[str, Any]]
) -> tuple[bool, str | None, str | None]:
    """(pinned, label, note) of a snapshot, annotations overriding the manifest."""
    item = notes.get(manifest["snapshot"], {})
    pinned = bool(item["pinned"]) if "pinned" in item else bool(manifest.get("pinned_at_creation"))
    label = item["label"] if "label" in item else manifest.get("label")
    return pinned, (label or None), item.get("note")

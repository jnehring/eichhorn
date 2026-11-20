"""Load YAML safely and remember the line of every value, for error messages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from eichhorn.errors import ConfigError

KeyPath = tuple[str | int, ...]


class Located:
    """Parsed YAML data plus the source line of each key path."""

    def __init__(self, file: Path, data: Any, lines: dict[KeyPath, int]) -> None:
        self.file = file
        self.data = data
        self.lines = lines

    def sub(self, path: KeyPath) -> Located:
        """The part of the document at `path`, with its own line numbers."""
        data = self.data
        for key in path:
            data = data[key]
        n = len(path)
        lines = {k[n:]: v for k, v in self.lines.items() if k[:n] == path}
        return Located(self.file, data, lines)

    def where(self, path: KeyPath) -> str:
        """`file:line` for a key path, falling back to the nearest parent."""
        p = path
        while p and p not in self.lines:
            p = p[:-1]
        line = self.lines.get(p)
        return f"{self.file}:{line}" if line else str(self.file)


def _build(node: yaml.Node, path: KeyPath, lines: dict[KeyPath, int], loader: Any) -> Any:
    lines[path] = node.start_mark.line + 1
    if isinstance(node, yaml.MappingNode):
        out: dict[Any, Any] = {}
        merged: list[yaml.Node] = []
        for key_node, value_node in node.value:
            is_merge = key_node.tag == "tag:yaml.org,2002:merge"
            key = "<<" if is_merge else loader.construct_object(key_node, deep=True)
            if key == "<<":  # YAML merge key: `<<: *anchor` or `<<: [*a, *b]`
                items = (
                    value_node.value if isinstance(value_node, yaml.SequenceNode) else [value_node]
                )
                for item in items:
                    if not isinstance(item, yaml.MappingNode):
                        raise ConfigError(
                            f"{loader.name}:{key_node.start_mark.line + 1}: `<<` needs a "
                            "mapping (an anchor such as *server)"
                        )
                    merged.append(item)
                continue
            if key in out:
                raise ConfigError(
                    f"{loader.name}:{key_node.start_mark.line + 1}: duplicate key {key!r}"
                )
            out[key] = _build(value_node, (*path, str(key)), lines, loader)
        for item in merged:  # keys written here win over merged ones
            for key, value in _build(item, path, {}, loader).items():
                if key not in out:
                    out[key] = value
                    lines.setdefault((*path, str(key)), item.start_mark.line + 1)
        lines[path] = node.start_mark.line + 1
        return out
    if isinstance(node, yaml.SequenceNode):
        return [_build(item, (*path, i), lines, loader) for i, item in enumerate(node.value)]
    return loader.construct_object(node, deep=True)


def load_text(text: str, file: Path) -> Located:
    loader = yaml.SafeLoader(text)
    loader.name = str(file)
    try:
        node = loader.get_single_node()
        lines: dict[KeyPath, int] = {}
        data = _build(node, (), lines, loader) if node is not None else None
    except yaml.YAMLError as exc:
        raise ConfigError(f"{file}: invalid YAML: {exc}") from exc
    finally:
        loader.dispose()
    return Located(file, data, lines)


def load_file(file: Path) -> Located:
    try:
        text = file.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {file}: {exc.strerror}") from exc
    return load_text(text, file)


def dump(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)

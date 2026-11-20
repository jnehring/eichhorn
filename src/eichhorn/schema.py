"""Declarative config schema.

The same definitions validate the YAML files and generate the reference tables
in docs/configuration.md (see `eichhorn._docs`).
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Any

from eichhorn.yamlload import KeyPath, Located

NAME_RE = re.compile(r"^[a-z0-9._-]+$")
DURATION_RE = re.compile(r"^(\d+)([smhdw])$")


@dataclass(frozen=True)
class Field:
    name: str
    kind: str  # str | bool | int | float | duration | list[str] | map | list[map] | any-map
    doc: str
    required: bool = False
    default: Any = None
    choices: tuple[str, ...] = ()
    fields: tuple[Field, ...] = ()  # for map and list[map]
    name_pattern: bool = False  # value must match NAME_RE
    milestone: str = ""  # set when a later milestone implements the behaviour


Schema = tuple[Field, ...]

RETENTION: Schema = (
    Field("keep_last", "int", "Keep the N newest snapshots."),
    Field("keep_daily", "int", "Keep the newest snapshot of each of the last N days."),
    Field("keep_weekly", "int", "The same per ISO week."),
    Field("keep_monthly", "int", "The same per month."),
    Field("keep_yearly", "int", "The same per year."),
)

ENGINES = ("auto", "builtin", "rsync")

GLOBAL_SCHEMA: Schema = (
    Field(
        "defaults",
        "map",
        "Defaults for all targets.",
        fields=(
            Field(
                "retention", "map", "Retention rules (pruning).", fields=RETENTION, milestone="3"
            ),
            Field(
                "checksums", "bool", "Write `checksums.sha256` into each snapshot.", default=True
            ),
            Field("engine", "str", "Transfer engine.", default="auto", choices=ENGINES),
            Field(
                "keep_awake", "bool", "Keep the computer from sleeping during runs.", default=True
            ),
            Field(
                "resume_max_age",
                "duration",
                "An unfinished run older than this is not resumed but set aside.",
                default="30d",
            ),
        ),
    ),
    Field(
        "repositories",
        "list[map]",
        "Known repositories (drives). `eichhorn repo init` adds entries to "
        "`repositories.yaml` for you; entries here are merged with those.",
        fields=(
            Field("id", "str", "The ID from the drive's `repository.yaml`.", required=True),
            Field("name", "str", "Human name, used with `--repo`.", required=True),
            Field("paths", "list[str]", "Extra places to look besides automatic discovery."),
        ),
    ),
    Field(
        "web",
        "map",
        "Web interface (`eichhorn serve`).",
        fields=(Field("listen", "str", "Address to listen on.", default="127.0.0.1:8765"),),
        milestone="6",
    ),
)

TARGET_SCHEMA: Schema = (
    Field("description", "str", "Free text shown in lists."),
    Field(
        "host",
        "str",
        "`local`, or an SSH host (alias from ~/.ssh/config or user@host).",
        default="local",
    ),
    Field(
        "sudo",
        "bool",
        "Run transfer, dumps and hooks with sudo on the host.",
        default=False,
    ),
    Field("engine", "str", "Transfer engine; overrides `defaults.engine`.", choices=ENGINES),
    Field("repositories", "list[str]", "Repository names this target goes to (default: all)."),
    Field(
        "retention",
        "map",
        "Retention rules; override `defaults.retention`.",
        fields=RETENTION,
        milestone="3",
    ),
    Field(
        "shell",
        "str",
        "Shell for hooks on a local Windows host.",
        choices=("cmd", "powershell"),
    ),
    Field("recipe", "str", "Name of a recipe that fills in sources, dumps and hooks."),
    Field("with", "any-map", "Parameters for the recipe."),
    Field(
        "sources",
        "list[map]",
        "Folders to back up.",
        fields=(
            Field(
                "name",
                "str",
                "Folder name inside the snapshot's `files/`.",
                required=True,
                name_pattern=True,
            ),
            Field(
                "path",
                "str",
                "Absolute path on the host. `~` works for local hosts.",
                required=True,
            ),
            Field("exclude", "list[str]", "Exclude patterns (see below)."),
        ),
    ),
    Field(
        "dumps",
        "list[map]",
        "Database dumps.",
        fields=(
            Field("name", "str", "File name inside `dumps/`.", required=True, name_pattern=True),
            Field(
                "type",
                "str",
                "Kind of dump.",
                required=True,
                choices=("postgres", "mysql", "command"),
            ),
            Field("container", "str", "Docker container running the database."),
            Field("database", "str", "Database name; `*` dumps all (postgres)."),
            Field("user", "str", "Database user."),
            Field(
                "password_env",
                "str",
                "Name of an environment variable (inside the container, or on the host) "
                "that holds the database password. The password itself never goes into "
                "the config.",
            ),
            Field("run", "str", "Command whose stdout is the dump (type: command)."),
            Field("ext", "str", "File extension for type: command.", default="dump"),
            Field(
                "delta",
                "map",
                "Store as a patch against a full dump.",
                milestone="5",
                fields=(
                    Field(
                        "full_every", "int", "A new full dump at least every N runs.", default=10
                    ),
                    Field(
                        "max_ratio", "float", "…or when a patch exceeds this share.", default=0.5
                    ),
                    Field(
                        "max_base_age",
                        "duration",
                        "…or when the full dump is older.",
                        default="90d",
                    ),
                ),
            ),
        ),
    ),
    Field(
        "hooks",
        "map",
        "Commands run on the host.",
        fields=(
            Field("pre", "list[str]", "Before dumps and transfer. A failure aborts the target."),
            Field("post", "list[str]", "After a successful run."),
            Field("post_always", "list[str]", "Always at the end, also after failures."),
        ),
    ),
)

BAD_EXCLUDE_PREFIXES = ("+ ", "- ", "merge ", ". ", ": ", "include ", "exclude ")


def _type_name(value: Any) -> str:
    if value is None:
        return "nothing (null)"
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, int):
        return "a number"
    if isinstance(value, float):
        return "a number"
    if isinstance(value, str):
        return "a string"
    if isinstance(value, list):
        return "a list"
    if isinstance(value, dict):
        return "a mapping"
    return type(value).__name__


def _hint(value: Any) -> str:
    if isinstance(value, bool):
        return " (YAML reads yes/no/on/off/true/false as booleans; put it in quotes)"
    return ""


def validate(doc: Located, schema: Schema) -> list[str]:
    """Return a list of problems, each prefixed with `file:line`."""
    problems: list[str] = []

    def problem(path: KeyPath, text: str) -> None:
        dotted = ".".join(str(p) for p in path) or "(top level)"
        problems.append(f"{doc.where(path)}: {dotted}: {text}")

    def check_map(value: Any, fields: Schema, path: KeyPath) -> None:
        if not isinstance(value, dict):
            problem(path, f"expected a mapping, got {_type_name(value)}")
            return
        known = {f.name: f for f in fields}
        for key in value:
            if key not in known:
                close = difflib.get_close_matches(str(key), list(known), n=1)
                tip = f" (did you mean {close[0]!r}?)" if close else ""
                problem((*path, str(key)), f"unknown key{tip}")
        for f in fields:
            if f.name not in value or value[f.name] is None:
                if f.required:
                    problem(path, f"missing required key {f.name!r}")
                continue
            check_value(value[f.name], f, (*path, f.name))

    def check_value(value: Any, f: Field, path: KeyPath) -> None:
        kind = f.kind
        if kind == "str":
            if not isinstance(value, str):
                problem(path, f"expected a string, got {_type_name(value)}{_hint(value)}")
                return
            if f.choices and value not in f.choices:
                problem(path, f"must be one of {', '.join(f.choices)}; got {value!r}")
            if f.name_pattern and not NAME_RE.match(value):
                problem(path, f"{value!r} may only contain a-z, 0-9, '.', '_' and '-'")
        elif kind == "bool":
            if not isinstance(value, bool):
                problem(path, f"expected true or false, got {_type_name(value)}")
        elif kind == "int":
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                problem(path, f"expected a whole number ≥ 0, got {value!r}")
        elif kind == "float":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                problem(path, f"expected a number, got {_type_name(value)}")
        elif kind == "duration":
            if not isinstance(value, str) or not DURATION_RE.match(value):
                problem(path, f"expected a duration like 30d, 12h or 2w; got {value!r}")
        elif kind == "list[str]":
            if not isinstance(value, list):
                problem(path, f"expected a list, got {_type_name(value)}")
                return
            for i, item in enumerate(value):
                if not isinstance(item, str):
                    problem((*path, i), f"expected a string, got {_type_name(item)}{_hint(item)}")
        elif kind == "map":
            check_map(value, f.fields, path)
        elif kind == "any-map":
            if not isinstance(value, dict):
                problem(path, f"expected a mapping, got {_type_name(value)}")
        elif kind == "list[map]":
            if not isinstance(value, list):
                problem(path, f"expected a list, got {_type_name(value)}")
                return
            seen: set[str] = set()
            for i, item in enumerate(value):
                check_map(item, f.fields, (*path, i))
                if (
                    isinstance(item, dict)
                    and isinstance(item.get("name"), str)
                    and any(sub.name == "name" and sub.name_pattern for sub in f.fields)
                ):
                    if item["name"] in seen:
                        problem((*path, i, "name"), f"duplicate name {item['name']!r}")
                    seen.add(item["name"])

    if doc.data is None:
        return problems
    check_map(doc.data, schema, ())
    return problems


def check_excludes(patterns: list[str]) -> list[str]:
    """Reject rsync filter syntax that the builtin engine does not understand."""
    errors = []
    for p in patterns:
        if p.startswith(BAD_EXCLUDE_PREFIXES):
            errors.append(f"{p!r}: rsync filter rules are not supported; write plain patterns")
        if not p.strip():
            errors.append("empty exclude pattern")
    return errors


def parse_duration(text: str) -> float:
    """Seconds for a duration like 30d."""
    m = DURATION_RE.match(text)
    if not m:
        raise ValueError(text)
    factor = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 7 * 86400}[m.group(2)]
    return int(m.group(1)) * factor


def defaults_of(fields: Schema) -> dict[str, Any]:
    return {f.name: f.default for f in fields if f.default is not None}


__all__ = [
    "GLOBAL_SCHEMA",
    "TARGET_SCHEMA",
    "Field",
    "check_excludes",
    "defaults_of",
    "parse_duration",
    "validate",
]

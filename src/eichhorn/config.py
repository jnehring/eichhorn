"""Loading and validating eichhorn.yaml, repositories.yaml and targets/*.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eichhorn import schema
from eichhorn.errors import ConfigError
from eichhorn.paths import config_dir
from eichhorn.yamlload import Located, dump, load_file

GLOBAL_FILE = "eichhorn.yaml"
REPOS_FILE = "repositories.yaml"  # written by `eichhorn repo init`
TARGETS_DIR = "targets"


@dataclass
class RepositoryRef:
    id: str
    name: str
    paths: list[Path] = field(default_factory=list)


@dataclass
class Defaults:
    retention: dict[str, int] = field(default_factory=dict)
    checksums: bool = True
    engine: str = "auto"
    keep_awake: bool = True
    resume_max_age: float = 30 * 86400


@dataclass
class Source:
    name: str
    path: str
    exclude: list[str] = field(default_factory=list)


@dataclass
class Target:
    name: str
    file: Path
    description: str = ""
    host: str = "local"
    sudo: bool = False
    engine: str = "auto"
    repositories: list[str] | None = None
    retention: dict[str, int] = field(default_factory=dict)
    sources: list[Source] = field(default_factory=list)
    dumps: list[dict[str, Any]] = field(default_factory=list)
    hooks: dict[str, list[str]] = field(default_factory=dict)
    recipe: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_local(self) -> bool:
        return self.host == "local"


@dataclass
class Config:
    dir: Path
    defaults: Defaults
    repositories: list[RepositoryRef]
    targets: dict[str, Target]
    web_listen: str = "127.0.0.1:8765"

    def repository(self, name: str) -> RepositoryRef:
        for r in self.repositories:
            if r.name == name or r.id == name:
                return r
        known = ", ".join(r.name for r in self.repositories) or "none"
        raise ConfigError(f"unknown repository {name!r} (known: {known})")

    def target(self, name: str) -> Target:
        try:
            return self.targets[name]
        except KeyError:
            known = ", ".join(sorted(self.targets)) or "none"
            raise ConfigError(f"unknown target {name!r} (known: {known})") from None


def _load_optional(path: Path) -> Located | None:
    return load_file(path) if path.exists() else None


def _parse_global(doc: Located | None) -> tuple[Defaults, list[RepositoryRef], str]:
    data: dict[str, Any] = (doc.data or {}) if doc else {}
    d = data.get("defaults") or {}
    defaults = Defaults(
        retention=dict(d.get("retention") or {}),
        checksums=d.get("checksums", True),
        engine=d.get("engine", "auto"),
        keep_awake=d.get("keep_awake", True),
        resume_max_age=schema.parse_duration(d.get("resume_max_age", "30d")),
    )
    repos = [
        RepositoryRef(r["id"], r["name"], [Path(p).expanduser() for p in r.get("paths") or []])
        for r in data.get("repositories") or []
    ]
    listen = (data.get("web") or {}).get("listen", "127.0.0.1:8765")
    return defaults, repos, listen


def _parse_target(name: str, doc: Located, defaults: Defaults) -> tuple[Target, list[str]]:
    data: dict[str, Any] = doc.data or {}
    problems: list[str] = []
    sources = []
    for i, s in enumerate(data.get("sources") or []):
        excludes = list(s.get("exclude") or [])
        problems += [
            f"{doc.where(('sources', i, 'exclude'))}: {e}" for e in schema.check_excludes(excludes)
        ]
        path = s["path"]
        host = data.get("host", "local")
        expanded = os.path.expanduser(path) if host == "local" else path
        if not (expanded.startswith("/") or Path(expanded).is_absolute()):
            problems.append(f"{doc.where(('sources', i, 'path'))}: path must be absolute: {path}")
        sources.append(Source(s["name"], expanded, excludes))
    if not sources and not data.get("dumps") and not data.get("recipe"):
        problems.append(f"{doc.file}: a target needs at least one source, dump or recipe")
    target = Target(
        name=name,
        file=doc.file,
        description=data.get("description", ""),
        host=data.get("host", "local"),
        sudo=data.get("sudo", False),
        engine=data.get("engine") or defaults.engine,
        repositories=data.get("repositories"),
        retention={**defaults.retention, **(data.get("retention") or {})},
        sources=sources,
        dumps=list(data.get("dumps") or []),
        hooks={k: list(v or []) for k, v in (data.get("hooks") or {}).items()},
        recipe=data.get("recipe"),
        raw=data,
    )
    return target, problems


def load_config(directory: Path | None = None, strict: bool = True) -> tuple[Config, list[str]]:
    """Load everything. With strict=True any problem raises ConfigError."""
    cdir = directory or config_dir()
    problems: list[str] = []
    gdoc = _load_optional(cdir / GLOBAL_FILE)
    rdoc = _load_optional(cdir / REPOS_FILE)
    if gdoc:
        problems += schema.validate(gdoc, schema.GLOBAL_SCHEMA)
    if rdoc:
        problems += schema.validate(rdoc, schema.GLOBAL_SCHEMA)
    if problems:
        # parsing would trip over the same problems; report what we have
        raise ConfigError("the configuration has problems:", problems)
    defaults, repos, listen = _parse_global(gdoc)
    _, managed, _ = _parse_global(rdoc)
    by_id = {r.id: r for r in repos}
    for r in managed:
        if r.id in by_id:
            by_id[r.id].paths += [p for p in r.paths if p not in by_id[r.id].paths]
        else:
            repos.append(r)
            by_id[r.id] = r
    names = [r.name for r in repos]
    for n in {n for n in names if names.count(n) > 1}:
        problems.append(f"two repositories are named {n!r}")

    targets: dict[str, Target] = {}
    tdir = cdir / TARGETS_DIR
    files = sorted(tdir.glob("*.yaml")) + sorted(tdir.glob("*.yml")) if tdir.is_dir() else []
    for f in files:
        name = f.stem
        if not schema.NAME_RE.match(name):
            problems.append(f"{f}: target file names may only contain a-z, 0-9, '.', '_', '-'")
            continue
        try:
            doc = load_file(f)
        except ConfigError as exc:
            problems.append(str(exc))
            continue
        tproblems = schema.validate(doc, schema.TARGET_SCHEMA)
        if tproblems:
            problems += tproblems
            continue
        target, more = _parse_target(name, doc, defaults)
        problems += more
        for rname in target.repositories or []:
            if rname not in names:
                problems.append(f"{doc.where(('repositories',))}: unknown repository {rname!r}")
        targets[name] = target
    cfg = Config(cdir, defaults, repos, targets, listen)
    if strict and problems:
        raise ConfigError("the configuration has problems:", problems)
    return cfg, problems


def register_repository(cdir: Path, ref: RepositoryRef) -> None:
    """Add or update an entry in repositories.yaml (machine-managed, no comments kept)."""
    path = cdir / REPOS_FILE
    data: dict[str, Any] = {}
    if path.exists():
        data = load_file(path).data or {}
    entries = [e for e in data.get("repositories") or [] if e.get("id") != ref.id]
    entry: dict[str, Any] = {"id": ref.id, "name": ref.name}
    if ref.paths:
        entry["paths"] = [str(p) for p in ref.paths]
    entries.append(entry)
    header = (
        "# Managed by `eichhorn repo init`. You may edit it; comments are not kept.\n"
        "# Repositories can also be listed in eichhorn.yaml.\n"
    )
    cdir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(header + dump({"repositories": entries}), encoding="utf-8")
    os.replace(tmp, path)


TARGET_TEMPLATE = """\
# Eichhorn target "{name}". Reference: docs/configuration.md
description: ""

# "local" for this computer, or an SSH host such as web1 or user@example.org
host: local

# Folders to back up. Each one becomes files/<name>/ in the snapshot.
sources:
  - name: documents
    path: ~/Documents
    exclude:
      - .DS_Store
      - Thumbs.db

# Optional: only these repositories (default: all)
# repositories: [Backup-A]
"""

"""The backup run workflow (spec 5.1)."""

from __future__ import annotations

import json
import platform
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from eichhorn import __version__, cache
from eichhorn.config import Config, Target
from eichhorn.connections import connect
from eichhorn.engines import Previous, choose_engine
from eichhorn.errors import EichhornError, NotYetSupported
from eichhorn.index import (
    CHECKSUMS_NAME,
    INDEX_NAME,
    Entry,
    load_index,
    write_checksums,
    write_index,
)
from eichhorn.lock import RepositoryLock
from eichhorn.stores.localfs import INCOMPLETE_PREFIX, LocalRepository, write_atomic
from eichhorn.summary import format_summary

TS_FORMAT = "%Y-%m-%dT%H-%M-%S"
RESUME_FILE = ".eichhorn-resume.json"


@dataclass
class RunOptions:
    label: str | None = None
    pin: bool = False
    restart: bool = False
    dry_run: bool = False
    command: str = "eichhorn run"


@dataclass
class RunResult:
    target: str
    repository: str
    ok: bool
    manifest: dict[str, Any] | None = None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


def _now() -> datetime:
    return datetime.now().astimezone()


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def check_supported(target: Target) -> None:
    """Fail before anything is written if the target needs a later milestone."""
    if not target.is_local:
        raise NotYetSupported(
            f"target {target.name!r}: SSH hosts arrive in milestone 2; only host: local works yet"
        )
    if target.dumps:
        raise NotYetSupported(f"target {target.name!r}: dumps arrive in milestone 2")
    if any(target.hooks.values()):
        raise NotYetSupported(f"target {target.name!r}: hooks arrive in milestone 2")
    if target.recipe:
        raise NotYetSupported(f"target {target.name!r}: recipes arrive in milestone 2")
    if target.sudo:
        raise NotYetSupported(f"target {target.name!r}: sudo arrives in milestone 2")


def _pick_incomplete(
    repo: LocalRepository, target: str, opts: RunOptions, max_age: float
) -> tuple[Path | None, list[str]]:
    notes: list[str] = []
    leftovers = repo.incomplete(target)
    if opts.restart:
        for p in leftovers:
            shutil.rmtree(p)
            notes.append(f"removed unfinished run {p.name} (--restart)")
        return None, notes
    keep: Path | None = None
    for p in reversed(leftovers):  # newest first
        try:
            started = datetime.strptime(p.name.removeprefix(INCOMPLETE_PREFIX), TS_FORMAT)
        except ValueError:
            continue
        too_old = (datetime.now() - started).total_seconds() > max_age
        if keep is None and not too_old:
            keep = p
        else:
            repo.abandon(p)
            notes.append(
                f"set aside an old unfinished run as {p.name.replace('incomplete', 'abandoned')}"
            )
    return keep, notes


def run_target(cfg: Config, target: Target, repo: LocalRepository, opts: RunOptions) -> RunResult:
    check_supported(target)
    t0 = time.monotonic()
    timings: dict[str, float] = {}
    caps = repo.capabilities
    engine = choose_engine(target.engine, caps)
    conn = connect(target.host)
    result = RunResult(target.name, repo.name, ok=False)

    with RepositoryLock(repo.root, f"{opts.command} {target.name}"):
        # ---- preflight ---------------------------------------------------
        warnings: list[str] = []
        free_before = repo.free_bytes()
        if free_before < 0.1 * repo.total_bytes():
            warnings.append(f"less than 10 % free on {repo.name}")
        for s in target.sources:
            if not conn.exists(s.path):
                raise EichhornError(
                    f"target {target.name!r}: source {s.name!r} not found: {s.path}"
                )
        incomplete, notes = _pick_incomplete(repo, target.name, opts, cfg.defaults.resume_max_age)
        warnings += notes
        started = _now()
        resumed = 0
        if incomplete is not None:
            snapshot = incomplete.name.removeprefix(INCOMPLETE_PREFIX)
            try:
                info = json.loads((incomplete / RESUME_FILE).read_text(encoding="utf-8"))
                resumed = int(info.get("resumed", 0)) + 1
                started = datetime.fromisoformat(info["started"])
            except (OSError, ValueError, KeyError):
                resumed = 1
        else:
            snapshot = started.strftime(TS_FORMAT)
            while repo.snapshot_dir(target.name, snapshot).exists():
                time.sleep(1)
                started = _now()
                snapshot = started.strftime(TS_FORMAT)
            incomplete = repo.new_incomplete(target.name, snapshot)
        write_atomic(
            incomplete / RESUME_FILE,
            json.dumps({"started": _iso(started), "resumed": resumed}),
        )
        latest = repo.latest(target.name)
        prev_dir = repo.snapshot_dir(target.name, latest) if latest else None
        prev = Previous(prev_dir, load_index(prev_dir) if prev_dir else {})
        timings["preflight"] = time.monotonic() - t0

        # ---- transfer ----------------------------------------------------
        t = time.monotonic()
        entries: list[Entry] = []
        sources_manifest = []
        status = "ok"
        for s in target.sources:
            src_entries, stats = engine.transfer(conn, s, incomplete, prev)
            entries += src_entries
            sources_manifest.append(stats.manifest())
            warnings += [f"{s.name}: {w}" for w in stats.warnings]
            if stats.status != "ok":
                status = "partial"
        timings["transfer"] = time.monotonic() - t

        # ---- checksums, index, manifest ----------------------------------
        t = time.monotonic()
        n_checksums = 0
        if cfg.defaults.checksums:
            n_checksums = write_checksums(incomplete / CHECKSUMS_NAME, entries)
        write_index(incomplete / INDEX_NAME, entries)
        timings["checksums"] = time.monotonic() - t

        t = time.monotonic()
        finished = _now()
        timings["finish"] = 0.0
        timings["total"] = time.monotonic() - t0
        manifest: dict[str, Any] = {
            "format": 1,
            "target": target.name,
            "snapshot": snapshot,
            "status": status,
            "engine": engine.name,
            "storage": caps.storage,
            "started": _iso(started),
            "finished": _iso(finished),
            "host": target.host,
            "repository_id": repo.id,
            "repository_name": repo.name,
            "copied_from": None,
            "label": opts.label,
            "pinned_at_creation": opts.pin,
            "resumed": resumed,
            "eichhorn_version": __version__,
            "platform": sys.platform,
            "machine": platform.node(),
            "sources": sources_manifest,
            "dumps": [],
            "hooks": [],
            "checksums": {
                "file": CHECKSUMS_NAME if cfg.defaults.checksums else None,
                "files": n_checksums,
            },
            "timings": {k: round(v, 2) for k, v in timings.items()},
            "repository": {"free_before": free_before, "free_after": 0},
            "warnings": warnings,
        }
        (incomplete / RESUME_FILE).unlink(missing_ok=True)
        manifest["repository"]["free_after"] = repo.free_bytes()
        manifest["timings"]["finish"] = round(time.monotonic() - t, 2)
        manifest["timings"]["total"] = round(time.monotonic() - t0, 2)
        write_atomic(
            incomplete / "snapshot.json", json.dumps(manifest, indent=2, ensure_ascii=False)
        )
        repo.finish(target.name, incomplete, snapshot)
        cache.record(repo, target.name, manifest)
        _write_log(repo, manifest)

    result.ok = status == "ok"
    result.manifest = manifest
    result.warnings = warnings
    return result


def _write_log(repo: LocalRepository, manifest: dict[str, Any]) -> None:
    try:
        log = repo.logs_dir() / f"{manifest['snapshot']}-{manifest['target']}.log"
        import re

        text = re.sub(r"\033\[[0-9;]*m", "", format_summary(manifest))
        all_warnings = "\n".join(f"warning: {w}" for w in manifest.get("warnings", []))
        log.write_text(text + "\n" + all_warnings + "\n", encoding="utf-8")
    except OSError:
        pass

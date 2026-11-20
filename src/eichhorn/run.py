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

from eichhorn import __version__, cache, prune
from eichhorn.config import Config, Target
from eichhorn.connections import Connection, connect
from eichhorn.content import Resolver
from eichhorn.dumps import DeltaBase, DumpResult, delta_names, run_dump
from eichhorn.engines import Previous, choose_engine
from eichhorn.errors import EichhornError
from eichhorn.hooks import HookResult, run_phase
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
    """Fail before anything is written if the target needs something not built yet."""
    return None


def _delta_base(
    repo: LocalRepository, target: str, latest: str | None, prev: Previous, dump: dict[str, Any]
) -> DeltaBase | None:
    """The full dump of an earlier snapshot that this run's patch may refer to."""
    if not latest or not dump.get("delta"):
        return None
    try:
        prev_dumps = {d["name"]: d for d in repo.manifest(target, latest).get("dumps", [])}
    except EichhornError:
        return None
    info = (prev_dumps.get(dump["name"]) or {}).get("delta")
    if not info:
        return None
    entry = prev.index.get(info["base_file"])
    if entry is None or entry.p != f"dumps/{delta_names(dump)[0]}":
        return None
    try:
        paths = Resolver(repo, target).locate(latest, entry)
    except EichhornError:
        return None
    return DeltaBase(
        info["base_snapshot"],
        paths,
        entry,
        int(info["runs_since_full"]),
        int(info.get("base_raw_bytes", 0)),
    )


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


def _post_always(conn: Connection, target: Target) -> list[HookResult]:
    return run_phase(
        conn, "post_always", target.hooks.get("post_always", []), target.sudo, stop_on_error=False
    )


def _failed_text(results: list[HookResult]) -> str:
    return "; ".join(
        f"{h.phase} hook {h.command!r} failed (exit {h.exit_code}): {h.output[-500:]}"
        for h in results
        if not h.ok
    )


def run_target(cfg: Config, target: Target, repo: LocalRepository, opts: RunOptions) -> RunResult:
    check_supported(target)
    t0 = time.monotonic()
    timings: dict[str, float] = {}
    caps = repo.capabilities
    conn = connect(target.host, sudo=target.sudo, shell=target.shell)
    engine = choose_engine(target.engine, caps, conn)
    result = RunResult(target.name, repo.name, ok=False)
    hooks: list[HookResult] = []

    with RepositoryLock(repo.root, f"{opts.command} {target.name}"):
        # ---- preflight ---------------------------------------------------
        warnings: list[str] = []
        if prune.recover(repo, target.name):
            warnings.append("finished an interrupted prune")
        free_before = repo.free_bytes()
        if free_before < 0.1 * repo.total_bytes():
            warnings.append(f"less than 10 % free on {repo.name}")
        for s in target.sources:
            if not conn.exists(s.path):
                raise EichhornError(
                    f"target {target.name!r}: source {s.name!r} not found on "
                    f"{target.host}: {s.path}"
                )
        timings["preflight"] = time.monotonic() - t0

        # ---- pre hooks ---------------------------------------------------
        t = time.monotonic()
        pre = run_phase(conn, "pre", target.hooks.get("pre", []), target.sudo, stop_on_error=True)
        hooks += pre
        timings["hooks_pre"] = time.monotonic() - t
        status = "ok"
        try:
            if any(not h.ok for h in pre):
                raise EichhornError(f"target {target.name!r}: {_failed_text(pre)}")

            incomplete, notes = _pick_incomplete(
                repo, target.name, opts, cfg.defaults.resume_max_age
            )
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

            # ---- dumps (always made again, also when resuming) ------------
            t = time.monotonic()
            entries: list[Entry] = []
            dump_results: list[DumpResult] = []
            dumps_dir = incomplete / "dumps"
            if dumps_dir.exists():
                shutil.rmtree(dumps_dir)
            if target.dumps:
                dumps_dir.mkdir()
                entries.append(Entry("dumps", "dir"))
            for d in target.dumps:
                base = _delta_base(repo, target.name, latest, prev, d)
                res = run_dump(conn, d, dumps_dir, target.sudo, snapshot, caps.hardlinks, base)
                dump_results.append(res)
                if res.status != "ok":
                    status = "partial"
                    warnings.append(f"dump {res.name} failed: {res.error}")
                else:
                    entries += res.entries
            timings["dumps"] = time.monotonic() - t

            # ---- transfer ------------------------------------------------
            t = time.monotonic()
            sources_manifest = []
            for s in target.sources:
                src_entries, stats = engine.transfer(conn, s, incomplete, prev)
                entries += src_entries
                sources_manifest.append(stats.manifest())
                warnings += [f"{s.name}: {w}" for w in stats.warnings]
                if stats.status != "ok":
                    status = "partial"
            timings["transfer"] = time.monotonic() - t

            # ---- post hooks ----------------------------------------------
            t = time.monotonic()
            post = run_phase(
                conn, "post", target.hooks.get("post", []), target.sudo, stop_on_error=False
            )
            hooks += post
        except BaseException as exc:
            always = _post_always(conn, target)
            failed = _failed_text(always)
            if failed and isinstance(exc, EichhornError):
                raise EichhornError(f"{exc}; also: {failed}") from exc
            raise
        always = _post_always(conn, target)
        hooks += always
        for h in post + always:
            if not h.ok:
                status = "partial"
                warnings.append(_failed_text([h]))
        timings["hooks_post"] = time.monotonic() - t

        # ---- checksums, index, manifest ----------------------------------
        t = time.monotonic()
        n_checksums = 0
        if cfg.defaults.checksums:
            n_checksums = write_checksums(incomplete / CHECKSUMS_NAME, entries)
        write_index(incomplete / INDEX_NAME, entries)
        timings["checksums"] = time.monotonic() - t

        t = time.monotonic()
        finished = _now()
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
            "dumps": [d.manifest() for d in dump_results],
            "hooks": [h.manifest() for h in hooks],
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

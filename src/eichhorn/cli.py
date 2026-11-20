"""Command-line interface (spec 6)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from eichhorn import __version__, cache, repository
from eichhorn import output as o
from eichhorn.awake import KeepAwake
from eichhorn.config import (
    TARGET_TEMPLATE,
    TARGETS_DIR,
    Config,
    RepositoryRef,
    load_config,
    register_repository,
)
from eichhorn.connections import connect
from eichhorn.engines import Previous
from eichhorn.errors import EXIT_FAILED, EXIT_OK, EXIT_USAGE, EichhornError, RepositoryError
from eichhorn.excludes import Excludes
from eichhorn.index import load_index
from eichhorn.lock import LOCK_NAME, is_stale, read_lock
from eichhorn.paths import config_dir
from eichhorn.run import RunOptions, check_supported, run_target
from eichhorn.stores.localfs import LocalRepository
from eichhorn.summary import changes_line, format_summary, totals


def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _cfg(args: argparse.Namespace, strict: bool = True) -> Config:
    cfg, _ = load_config(args.config, strict=strict)
    return cfg


# ---------------------------------------------------------------- repo
def cmd_repo_init(args: argparse.Namespace) -> int:
    cdir: Path = args.config or config_dir()
    cfg = _cfg(args)
    if any(r.name == args.name for r in cfg.repositories):
        raise RepositoryError(f"a repository named {args.name!r} is already registered")
    repo = repository.init_repository(Path(args.path), args.name, args.storage)
    paths = [] if repository.is_discoverable(repo.root) else [repo.root]
    register_repository(cdir, RepositoryRef(repo.id, repo.name, paths))
    m = repo.meta
    print(f"Created repository {o.bold(repo.name)} at {repo.root}")
    print(f"  file system  {m['filesystem']}  (case-sensitive: {m['case_sensitive']})")
    print(f"  storage      {m['storage']}")
    if m["storage"] == "sparse":
        print(
            o.yellow(
                "  No hardlinks here: each snapshot folder holds only new and changed "
                "files (spec 3.8)."
            )
        )
    if m["filesystem"] in ("exfat", "msdos", "vfat", "fat32"):
        print(
            o.dim(
                "  Tip: NTFS (Windows), APFS (macOS) or ext4 (Linux) give complete "
                "snapshot folders."
            )
        )
    if not m["case_sensitive"]:
        print(
            o.dim(
                "  Case-insensitive: files differing only in case are stored as "
                "'name (eichhorn-2)'."
            )
        )
    print(f"  registered in {cdir / 'repositories.yaml'}")
    return EXIT_OK


def cmd_repo_list(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    rows, data = [], []
    for ref in cfg.repositories:
        repo = repository.locate(ref)
        if repo:
            free, total = repo.free_bytes(), repo.total_bytes()
            item = {
                "name": ref.name,
                "id": ref.id,
                "connected": True,
                "path": str(repo.root),
                "storage": repo.meta.get("storage"),
                "filesystem": repo.meta.get("filesystem"),
                "free_bytes": free,
                "total_bytes": total,
            }
            rows.append(
                [
                    ref.name,
                    o.green("connected"),
                    str(repo.root),
                    f"{repo.meta.get('filesystem')} · {repo.meta.get('storage')}",
                    f"{o.size(free)} free of {o.size(total)}",
                ]
            )
        else:
            item = {"name": ref.name, "id": ref.id, "connected": False}
            rows.append([ref.name, o.dim("not connected"), "", "", ""])
        data.append(item)
    if args.json:
        _print_json(data)
    elif not rows:
        print("No repositories yet. Create one with: eichhorn repo init PATH --name NAME")
    else:
        print(o.table(rows, ["NAME", "STATE", "PATH", "FILE SYSTEM", "SPACE"]))
    return EXIT_OK


def _connected_repo(cfg: Config, name: str) -> LocalRepository:
    ref = cfg.repository(name)
    repo = repository.locate(ref)
    if repo is None:
        raise RepositoryError(f"repository {ref.name!r} is not connected")
    return repo


def cmd_repo_scan(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    repos = [_connected_repo(cfg, args.repo)] if args.repo else repository.connected(cfg)
    if not repos:
        raise RepositoryError("no repository is connected")
    for repo in repos:
        n = cache.rescan(repo)
        print(f"{repo.name}: {n} target(s) indexed")
    return EXIT_OK


def cmd_repo_unlock(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    repo = _connected_repo(cfg, args.repo)
    info = read_lock(repo.root)
    if info is None:
        print(f"{repo.name} is not locked")
        return EXIT_OK
    if not is_stale(info) and not args.force:
        raise RepositoryError(
            f"the lock belongs to a process that may still run ({info.get('command')!r}, "
            f"pid {info.get('pid')} on {info.get('host')}). Use --force if you are sure."
        )
    (repo.root / LOCK_NAME).unlink(missing_ok=True)
    print(f"removed the lock on {repo.name}")
    return EXIT_OK


# ---------------------------------------------------------------- target / config
def cmd_target_list(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    state = cache.load()
    rows, data = [], []
    for name, t in sorted(cfg.targets.items()):
        last = []
        for ref in cfg.repositories:
            if t.repositories and ref.name not in t.repositories:
                continue
            info = state.get("repositories", {}).get(ref.id, {}).get("targets", {}).get(name)
            if info:
                last.append({"repository": ref.name, **info})
        data.append(
            {
                "name": name,
                "host": t.host,
                "engine": t.engine,
                "description": t.description,
                "last": last,
            }
        )
        newest = max(last, key=lambda i: i["finished"], default=None)
        rows.append(
            [
                name,
                t.host,
                t.engine,
                f"{newest['repository']}: {o.age(newest['finished'])}"
                if newest
                else o.dim("never"),
                t.description,
            ]
        )
    if args.json:
        _print_json(data)
    elif not rows:
        print(
            f"No targets yet. Create one with: eichhorn target new NAME  ({cfg.dir / TARGETS_DIR})"
        )
    else:
        print(o.table(rows, ["TARGET", "HOST", "ENGINE", "LAST BACKUP", "DESCRIPTION"]))
    return EXIT_OK


def cmd_target_new(args: argparse.Namespace) -> int:
    if args.recipe:
        raise EichhornError("recipes arrive in milestone 2")
    from eichhorn.schema import NAME_RE

    if not NAME_RE.match(args.name):
        raise EichhornError("target names may only contain a-z, 0-9, '.', '_' and '-'")
    cdir: Path = args.config or config_dir()
    f = cdir / TARGETS_DIR / f"{args.name}.yaml"
    if f.exists():
        raise EichhornError(f"{f} already exists")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(TARGET_TEMPLATE.format(name=args.name), encoding="utf-8")
    print(f"Created {f}\nEdit it, then check it with: eichhorn config check")
    return EXIT_OK


def cmd_target_show(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    t = cfg.target(args.name)
    if args.expanded and t.recipe:
        raise EichhornError("recipes arrive in milestone 2")
    print(o.dim(f"# {t.file}"))
    print(t.file.read_text(encoding="utf-8").rstrip())
    return EXIT_OK


def cmd_config_check(args: argparse.Namespace) -> int:
    cfg, problems = load_config(args.config, strict=False)
    if problems:
        print(o.red(f"{len(problems)} problem(s):"))
        for p in problems:
            print(f"  - {p}")
        return EXIT_USAGE
    print(
        o.green("ok") + f": {len(cfg.repositories)} repositories, "
        f"{len(cfg.targets)} targets in {cfg.dir}"
    )
    return EXIT_OK


# ---------------------------------------------------------------- run
def _dry_run(cfg: Config, names: list[str], repo: LocalRepository) -> int:
    for name in names:
        t = cfg.target(name)
        check_supported(t)
        conn = connect(t.host)
        latest = repo.latest(name)
        prev_dir = repo.snapshot_dir(name, latest) if latest else None
        prev = Previous(prev_dir, load_index(prev_dir) if prev_dir else {})
        print(o.bold(f"{name} → {repo.name}") + o.dim("  (dry run, nothing is written)"))
        for s in t.sources:
            listing = conn.list(s.path, Excludes(s.exclude))
            new = changed = unchanged = 0
            nbytes = 0
            seen = set()
            for e in listing.entries:
                if e.type != "file":
                    continue
                p = f"files/{s.name}/{e.rel}"
                seen.add(p)
                old = prev.index.get(p)
                if old is None:
                    new += 1
                    nbytes += e.size
                elif old.size == e.size and old.mtime_ns == e.mtime_ns:
                    unchanged += 1
                else:
                    changed += 1
                    nbytes += e.size
            deleted = sum(
                1
                for p, old in prev.index.items()
                if old.type == "file" and p.startswith(f"files/{s.name}/") and p not in seen
            )
            ch = {"new": new, "changed": changed, "deleted": deleted}
            print(
                f"  {s.name}  {changes_line(ch)}   {o.size(nbytes)} to copy, {unchanged} unchanged"
            )
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    if args.all_repos or (args.repo and len(args.repo) > 1):
        raise EichhornError("backing up to several repositories in one run arrives in milestone 4")
    names = sorted(cfg.targets) if args.all else args.targets
    if not names:
        raise EichhornError("name one or more targets, or use --all")
    targets = [cfg.target(n) for n in names]
    for t in targets:
        check_supported(t)
    failures = 0
    results = []
    with KeepAwake(enabled=cfg.defaults.keep_awake and not args.allow_sleep) as awake:
        if awake.note and not args.quiet:
            print(o.dim(awake.note))
        for t in targets:
            repos = repository.choose(cfg, args.repo, t.repositories)
            repo = repos[0]
            if args.dry_run:
                _dry_run(cfg, [t.name], repo)
                continue
            if not args.quiet and not args.json:
                print(o.dim(f"backing up {t.name} → {repo.name} …"), flush=True)
            try:
                res = run_target(
                    cfg,
                    t,
                    repo,
                    RunOptions(
                        label=args.label, pin=args.pin, restart=args.restart, command="eichhorn run"
                    ),
                )
            except KeyboardInterrupt:
                print(o.yellow(f"\ninterrupted: {t.name} stays unfinished and resumes next time"))
                return 130
            except EichhornError as exc:
                failures += 1
                print(o.red(f"{t.name}: {exc}"), file=sys.stderr)
                results.append({"target": t.name, "ok": False, "error": str(exc)})
                continue
            results.append({"target": t.name, "ok": res.ok, "manifest": res.manifest})
            if not res.ok:
                failures += 1
            if res.manifest and not args.json:
                print(format_summary(res.manifest))
    if args.json:
        _print_json(results)
    return EXIT_FAILED if failures else EXIT_OK


# ---------------------------------------------------------------- status / snapshots
def cmd_status(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    state = cache.load()
    rows, data = [], []
    live = {ref.id: repository.locate(ref) for ref in cfg.repositories}
    for name, t in sorted(cfg.targets.items()):
        for ref in cfg.repositories:
            if t.repositories and ref.name not in t.repositories:
                continue
            repo = live[ref.id]
            info: dict[str, Any] | None = None
            unfinished = False
            if repo:
                latest = repo.latest(name)
                if latest:
                    m = repo.manifest(name, latest)
                    info = {
                        "snapshot": latest,
                        "status": m["status"],
                        "finished": m["finished"],
                        "count": len(repo.snapshots(name)),
                    }
                unfinished = bool(repo.incomplete(name))
            else:
                info = state.get("repositories", {}).get(ref.id, {}).get("targets", {}).get(name)
            data.append(
                {
                    "target": name,
                    "repository": ref.name,
                    "connected": repo is not None,
                    "last": info,
                    "unfinished": unfinished,
                }
            )
            rows.append(
                [
                    name,
                    ref.name + ("" if repo else o.dim(" (unplugged)")),
                    info["snapshot"] if info else o.dim("never"),
                    o.age(info["finished"]) if info else "",
                    o.status_color(info["status"]) if info else "",
                    str(info.get("count", "")) if info else "",
                    o.yellow("unfinished run") if unfinished else "",
                ]
            )
    if args.json:
        _print_json(data)
    elif not rows:
        print("Nothing to show yet: add a repository and a target first.")
    else:
        print(
            o.table(
                rows, ["TARGET", "REPOSITORY", "LAST SNAPSHOT", "AGE", "STATUS", "SNAPSHOTS", ""]
            )
        )
    return EXIT_OK


def cmd_snapshots(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    cfg.target(args.target)
    repos = [_connected_repo(cfg, args.repo)] if args.repo else repository.connected(cfg)
    if not repos:
        raise RepositoryError("no repository is connected")
    data = []
    for repo in repos:
        rows = []
        for snap in repo.snapshots(args.target):
            m = repo.manifest(args.target, snap)
            tt = totals(m)
            data.append(m)
            label = m.get("label") or ""
            if m.get("pinned_at_creation"):
                label = "📌 " + label
            rows.append(
                [
                    snap,
                    o.status_color(m["status"]),
                    str(tt["files"]),
                    o.size(tt["bytes"]),
                    changes_line(tt),
                    o.size(tt["stored_bytes"]),
                    o.duration(m.get("timings", {}).get("total", 0)),
                    label,
                ]
            )
        if not args.json:
            print(o.bold(f"{args.target} in {repo.name}") + o.dim(f"  ({repo.root})"))
            if rows:
                print(
                    o.table(
                        rows,
                        [
                            "SNAPSHOT",
                            "STATUS",
                            "FILES",
                            "SIZE",
                            "CHANGES",
                            "STORED",
                            "TIME",
                            "LABEL",
                        ],
                    )
                )
            else:
                print(o.dim("  no snapshots"))
    if args.json:
        _print_json(data)
    return EXIT_OK


# ---------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="eichhorn",
        description="Plain-file, hardlinked snapshot backups onto local drives.",
    )
    p.add_argument("--version", action="version", version=f"eichhorn {__version__}")
    p.add_argument(
        "--config",
        type=Path,
        metavar="DIR",
        help="config folder (default: the platform's config folder, or $EICHHORN_CONFIG)",
    )
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--quiet", "-q", action="store_true", help="less output")
    p.add_argument(
        "--allow-sleep", action="store_true", help="do not keep the computer awake during long runs"
    )
    sub = p.add_subparsers(dest="command", metavar="COMMAND", required=True)

    repo = sub.add_parser("repo", help="manage repositories (backup drives)")
    rsub = repo.add_subparsers(dest="repo_command", metavar="ACTION", required=True)
    r = rsub.add_parser("init", help="make a folder on a drive into a repository")
    r.add_argument("path", help="folder for the repository, e.g. /Volumes/Backup-A/eichhorn")
    r.add_argument("--name", required=True, help="a short name, e.g. Backup-A")
    r.add_argument(
        "--storage", choices=["hardlinks", "sparse"], help="override the detected storage mode"
    )
    r.set_defaults(func=cmd_repo_init)
    r = rsub.add_parser("list", help="list repositories and whether they are connected")
    r.set_defaults(func=cmd_repo_list)
    r = rsub.add_parser("scan", help="rebuild the local cache from a repository")
    r.add_argument("repo", nargs="?", help="repository name (default: all connected)")
    r.set_defaults(func=cmd_repo_scan)
    r = rsub.add_parser("unlock", help="remove a stale lock")
    r.add_argument("repo", help="repository name")
    r.add_argument("--force", action="store_true", help="also remove a lock that may be in use")
    r.set_defaults(func=cmd_repo_unlock)

    tgt = sub.add_parser("target", help="manage targets (things to back up)")
    tsub = tgt.add_subparsers(dest="target_command", metavar="ACTION", required=True)
    r = tsub.add_parser("list", help="list targets and their last backup")
    r.set_defaults(func=cmd_target_list)
    r = tsub.add_parser("new", help="create a commented target file")
    r.add_argument("name")
    r.add_argument("--recipe", help="start from a recipe (milestone 2)")
    r.set_defaults(func=cmd_target_new)
    r = tsub.add_parser("show", help="print a target's config")
    r.add_argument("name")
    r.add_argument("--expanded", action="store_true", help="with its recipe filled in")
    r.set_defaults(func=cmd_target_show)

    cfgp = sub.add_parser("config", help="configuration tools")
    csub = cfgp.add_subparsers(dest="config_command", metavar="ACTION", required=True)
    r = csub.add_parser("check", help="validate all config files")
    r.set_defaults(func=cmd_config_check)

    r = sub.add_parser("run", help="back up targets")
    r.add_argument("targets", nargs="*", metavar="TARGET")
    r.add_argument("--all", action="store_true", help="all targets")
    r.add_argument("--repo", action="append", metavar="NAME", help="repository to write to")
    r.add_argument(
        "--all-repos", action="store_true", help="every connected repository (milestone 4)"
    )
    r.add_argument("--label", help="a note for the new snapshot")
    r.add_argument("--pin", action="store_true", help="never prune the new snapshot")
    r.add_argument("--dry-run", action="store_true", help="show what would be copied")
    r.add_argument(
        "--restart", action="store_true", help="throw away an unfinished run instead of resuming it"
    )
    r.add_argument("--no-prune", action="store_true", help="skip pruning (milestone 3)")
    r.set_defaults(func=cmd_run)

    r = sub.add_parser("status", help="last backup of each target in each repository")
    r.set_defaults(func=cmd_status)

    r = sub.add_parser("snapshots", help="list the snapshots of a target")
    r.add_argument("target")
    r.add_argument("--repo", metavar="NAME")
    r.set_defaults(func=cmd_snapshots)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        code: int = args.func(args)
        return code
    except EichhornError as exc:
        print(o.red(f"error: {exc}"), file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print(o.yellow("\ninterrupted"), file=sys.stderr)
        return 130


__all__ = ["build_parser", "main"]

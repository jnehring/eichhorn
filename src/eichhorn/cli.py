"""Command-line interface (spec 6)."""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path
from typing import Any

from eichhorn import __version__, cache, recipes, repository
from eichhorn import notes as annotations_store
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
from eichhorn.yamlload import dump


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


def _recipe_template(name: str, recipe_name: str, cdir: Path) -> str:
    recipe = recipes.load(recipe_name, cdir)
    lines = [
        f'# Eichhorn target "{name}" from the recipe "{recipe_name}".',
        f"# {recipe.description}",
        "# Reference: docs/recipes/" + recipe_name + ".md",
        "",
        "# The server (an alias from ~/.ssh/config or user@host), or local",
        "host: my-server",
        "sudo: true",
        "",
        f"recipe: {recipe_name}",
        "with:",
    ]
    for p in recipe.params.values():
        lines.append(f"  # {p.doc}")
        if p.required:
            lines.append(f"  {p.name}: ''   # required")
        elif p.default is None:
            lines.append(f"  # {p.name}: ")
        else:
            lines.append(f"  # {p.name}: {dump(p.default).strip().replace(chr(10), ' ')}")
    lines += [
        "",
        "# You can add your own sources, dumps and hooks here; a source with the same",
        "# name as one from the recipe replaces it. See the result with:",
        f"#   eichhorn target show {name} --expanded",
    ]
    return "\n".join(lines) + "\n"


def cmd_target_new(args: argparse.Namespace) -> int:
    from eichhorn.schema import NAME_RE

    if not NAME_RE.match(args.name):
        raise EichhornError("target names may only contain a-z, 0-9, '.', '_' and '-'")
    cdir: Path = args.config or config_dir()
    f = cdir / TARGETS_DIR / f"{args.name}.yaml"
    if f.exists():
        raise EichhornError(f"{f} already exists")
    if args.recipe:
        try:
            text = _recipe_template(args.name, args.recipe, cdir)
        except KeyError as exc:
            raise EichhornError(exc.args[0]) from None
    else:
        text = TARGET_TEMPLATE.format(name=args.name)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")
    print(f"Created {f}\nEdit it, then check it with: eichhorn config check")
    return EXIT_OK


def cmd_target_show(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    t = cfg.target(args.name)
    if args.expanded:
        print(
            o.dim(f"# {t.file}, with recipe {t.recipe!r} filled in" if t.recipe else f"# {t.file}")
        )
        clean = {k: v for k, v in t.expanded.items() if v is not None}
        print(dump(clean).rstrip())
        return EXIT_OK
    print(o.dim(f"# {t.file}"))
    print(t.file.read_text(encoding="utf-8").rstrip())
    return EXIT_OK


def cmd_recipe_list(args: argparse.Namespace) -> int:
    cdir: Path = args.config or config_dir()
    data: list[dict[str, Any]] = []
    for name in recipes.all_names(cdir):
        r = recipes.load(name, cdir)
        data.append(
            {
                "name": name,
                "description": r.description,
                "origin": r.origin,
                "params": {
                    p.name: {"required": p.required, "default": p.default, "doc": p.doc}
                    for p in r.params.values()
                },
            }
        )
    if args.json:
        _print_json(data)
        return EXIT_OK
    for item in data:
        print(o.bold(item["name"]) + o.dim(f"  ({item['origin']})"))
        print(f"  {item['description']}")
        for pname, spec in item["params"].items():
            req = (
                o.yellow("required")
                if spec["required"]
                else (
                    f"default: {spec['default']}"
                    if spec["default"] not in (None, [])
                    else "optional"
                )
            )
            print(f"    {pname:<16} {req}  {o.dim(spec['doc'])}")
        print()
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace) -> int:
    from eichhorn import doctor

    cfg = _cfg(args)
    names = args.targets or sorted(cfg.targets)
    conn_repos = repository.connected(cfg)
    caps = conn_repos[0].capabilities if conn_repos else None
    failed = 0
    report: dict[str, Any] = {"local": [], "targets": {}}
    marks = {"ok": o.green("✓"), "warn": o.yellow("!"), "fail": o.red("✗")}
    local = doctor.local_checks()
    report["local"] = [c.__dict__ for c in local]
    if not args.json:
        print(o.bold("this computer"))
        for c in local:
            print(f"  {marks[c.state]} {c.name}: {c.detail}")
    for name in names:
        t = cfg.target(name)
        try:
            checks = doctor.check_target(t, caps)
        except EichhornError as exc:
            checks = [doctor.Check("connection", "fail", str(exc))]
        report["targets"][name] = [c.__dict__ for c in checks]
        failed += sum(c.state == "fail" for c in checks)
        if not args.json:
            print(o.bold(f"\n{name}") + o.dim(f"  ({t.host})"))
            for c in checks:
                print(f"  {marks[c.state]} {c.name}: {c.detail}")
    if args.json:
        _print_json(report)
    elif caps is None:
        print(o.dim("\n(no repository connected: the engine choice was not checked)"))
    return EXIT_FAILED if failed else EXIT_OK


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
        conn = connect(t.host, sudo=t.sudo, shell=t.shell)
        latest = repo.latest(name)
        prev_dir = repo.snapshot_dir(name, latest) if latest else None
        prev = Previous(prev_dir, load_index(prev_dir) if prev_dir else {})
        print(o.bold(f"{name} → {repo.name}") + o.dim("  (dry run, nothing is written)"))
        for s in t.sources:
            listing = conn.listing(s.path, Excludes(s.exclude))
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
    from eichhorn.content import Resolver
    from eichhorn.lock import RepositoryLock
    from eichhorn.replicate import copy_snapshot

    cfg = _cfg(args)
    names = sorted(cfg.targets) if args.all else args.targets
    if not names:
        raise EichhornError("name one or more targets, or use --all")
    targets = [cfg.target(n) for n in names]
    for t in targets:
        check_supported(t)
    failures = 0
    results: list[dict[str, Any]] = []
    interactive = sys.stdin.isatty() and not args.json
    with KeepAwake(enabled=cfg.defaults.keep_awake and not args.allow_sleep) as awake:
        if awake.note and not args.quiet:
            print(o.dim(awake.note))
        for t in targets:
            repos = repository.choose(cfg, args.repo, t.repositories, args.all_repos, interactive)
            repo = repos[0]
            if args.dry_run:
                _dry_run(cfg, [t.name], repo)
                continue
            if not args.quiet and not args.json:
                more = (
                    f" (then copied to {', '.join(r.name for r in repos[1:])})"
                    if (len(repos) > 1)
                    else ""
                )
                print(o.dim(f"backing up {t.name} → {repo.name}{more} …"), flush=True)
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
            item: dict[str, Any] = {
                "target": t.name,
                "ok": res.ok,
                "manifest": res.manifest,
                "copies": [],
            }
            results.append(item)
            if not res.ok:
                failures += 1
            if res.manifest and not args.json:
                print(format_summary(res.manifest))
            # further repositories get a local copy of the new snapshot (spec 5.6)
            if res.manifest and len(repos) > 1:
                resolver = Resolver(repo, t.name)
                for other in repos[1:]:
                    try:
                        with RepositoryLock(other.root, f"eichhorn run {t.name} (copy)"):
                            cr = copy_snapshot(resolver, res.manifest["snapshot"], other)
                    except EichhornError as exc:
                        failures += 1
                        print(o.red(f"  copy to {other.name} failed: {exc}"), file=sys.stderr)
                        item["copies"].append({"repository": other.name, "error": str(exc)})
                        continue
                    item["copies"].append(cr.as_dict())
                    if not args.json and not args.quiet:
                        print(
                            f"  {'copied to'.ljust(10)} {other.name}: {cr.copied} file(s), "
                            f"{o.size(cr.copied_bytes)} written, {cr.linked + cr.skipped_sparse}"
                            f" unchanged ({o.duration(cr.duration_s)})"
                        )
            if res.manifest and not args.no_prune and any(t.retention.values()):
                for r in repos:
                    pr = _prune_one(r, t.name, t.retention, dry_run=False)
                    if pr.delete and not args.json and not args.quiet:
                        print(
                            o.dim(
                                f"  pruned {len(pr.delete)} old snapshot(s) in {r.name}: "
                                f"{', '.join(pr.delete)}"
                            )
                        )
    if args.json:
        _print_json(results)
    return EXIT_FAILED if failures else EXIT_OK


def cmd_repo_sync(args: argparse.Namespace) -> int:
    from eichhorn.replicate import sync

    cfg = _cfg(args)
    a = _connected_repo(cfg, args.source)
    b = _connected_repo(cfg, args.destination)
    if a.id == b.id:
        raise EichhornError("source and destination are the same repository")
    pairs = [(a, b), (b, a)] if args.both else [(a, b)]
    out: list[dict[str, Any]] = []
    failed = 0
    with KeepAwake(enabled=cfg.defaults.keep_awake and not args.allow_sleep):
        for src, dst in pairs:
            names = args.target or [
                n
                for n, t in sorted(cfg.targets.items())
                if not t.repositories or (src.name in t.repositories and dst.name in t.repositories)
            ]
            names = [n for n in names if src.snapshots(n)]
            try:
                plans, copied = sync(src, dst, names, args.include_older, args.dry_run)
            except EichhornError as exc:
                failed += 1
                print(o.red(f"{src.name} → {dst.name}: {exc}"), file=sys.stderr)
                continue
            out.append(
                {
                    "from": src.name,
                    "to": dst.name,
                    "plans": [p.__dict__ for p in plans],
                    "copied": [c.as_dict() for c in copied],
                }
            )
            if not args.json:
                verb = "would copy" if args.dry_run else "copied"
                print(o.bold(f"{src.name} → {dst.name}"))
                for p in plans:
                    line = f"  {p.target}: {verb} {len(p.copy)} snapshot(s)"
                    if p.skipped_older:
                        line += o.dim(
                            f", {len(p.skipped_older)} older than {dst.name}'s oldest "
                            "skipped (--include-older)"
                        )
                    print(line)
                for c in copied:
                    print(
                        o.dim(
                            f"    {c.snapshot}: {c.copied} file(s) written "
                            f"({o.size(c.copied_bytes)}), {c.linked + c.skipped_sparse} "
                            "unchanged"
                        )
                    )
            if not args.dry_run and not args.no_prune:
                for p in plans:
                    t = cfg.targets.get(p.target)
                    if t and p.copy and any(t.retention.values()):
                        _prune_one(dst, p.target, t.retention, dry_run=False)
    if args.json:
        _print_json(out)
    return EXIT_FAILED if failed else EXIT_OK


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
        notes = annotations_store.load(repo, args.target)
        for snap in repo.snapshots(args.target):
            m = repo.manifest(args.target, snap)
            tt = totals(m)
            pinned, label_text, note = annotations_store.effective(m, notes)
            data.append({**m, "pinned": pinned, "label": label_text, "note": note})
            label = label_text or ""
            if pinned:
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


# ---------------------------------------------------------------- history (milestone 3)
def _prune_one(repo: LocalRepository, target: str, rules: dict[str, int], dry_run: bool) -> Any:
    from eichhorn.lock import RepositoryLock
    from eichhorn.prune import prune_target

    if dry_run:
        return prune_target(repo, target, rules, dry_run=True)
    with RepositoryLock(repo.root, f"eichhorn prune {target}"):
        return prune_target(repo, target, rules)


def _repos_for(cfg: Config, name: str | None) -> list[LocalRepository]:
    repos = [_connected_repo(cfg, name)] if name else repository.connected(cfg)
    if not repos:
        raise RepositoryError("no repository is connected")
    return repos


def cmd_prune(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    names = args.targets or sorted(cfg.targets)
    results = []
    for repo in _repos_for(cfg, args.repo):
        for name in names:
            t = cfg.target(name)
            if not repo.snapshots(name):
                continue
            if not any(t.retention.values()):
                if not args.json:
                    print(o.dim(f"{name} in {repo.name}: no retention rules, nothing to prune"))
                continue
            r = _prune_one(repo, name, t.retention, args.dry_run)
            results.append(r.as_dict())
            if args.json:
                continue
            head = "would delete" if args.dry_run else "deleted"
            moved = f", moved {r.moved_files} file(s) forward" if r.moved_files else ""
            print(
                o.bold(f"{name} in {repo.name}") + f": keep {len(r.keep)}, "
                f"{head} {len(r.delete)}{moved}"
            )
            for snap in sorted(set(r.keep) | set(r.delete)):
                if snap in r.keep:
                    print(f"  {o.green('keep')}    {snap}  {o.dim(', '.join(r.keep[snap]))}")
                else:
                    print(f"  {o.red('delete')}  {snap}")
            if r.removed_abandoned:
                print(o.dim(f"  {r.removed_abandoned} abandoned unfinished run(s) removed"))
    if args.json:
        _print_json(results)
    return EXIT_OK


def _resolver(cfg: Config, target: str, repo_name: str | None) -> Any:
    from eichhorn.content import Resolver

    cfg.target(target)
    repos = _repos_for(cfg, repo_name)
    with_snaps = [r for r in repos if r.snapshots(target)]
    if not with_snaps:
        raise EichhornError(f"{target} has no snapshots in the connected repositories")
    if len(with_snaps) > 1 and not repo_name:
        names = ", ".join(r.name for r in with_snaps)
        raise RepositoryError(f"{target} is in several repositories ({names}); choose with --repo")
    return Resolver(with_snaps[0], target)


def cmd_pin(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    res = _resolver(cfg, args.target, args.repo)
    snap = res.resolve_name(args.snapshot)
    pinned = args.command == "pin"
    annotations_store.set_pin(res.repo, args.target, snap, pinned, getattr(args, "note", None))
    print(f"{'pinned' if pinned else 'unpinned'} {args.target} {snap} in {res.repo.name}")
    return EXIT_OK


def cmd_label(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    res = _resolver(cfg, args.target, args.repo)
    snap = res.resolve_name(args.snapshot)
    annotations_store.set_label(res.repo, args.target, snap, args.text)
    print(f"label of {args.target} {snap}: {args.text or '(none)'}")
    return EXIT_OK


def cmd_diff(args: argparse.Namespace) -> int:
    from eichhorn.history import diff

    cfg = _cfg(args)
    res = _resolver(cfg, args.target, args.repo)
    snaps = res.snapshots
    if args.snap_b:
        a, b = res.resolve_name(args.snap_a), res.resolve_name(args.snap_b)
    elif args.snap_a:
        b = res.resolve_name(args.snap_a)
        i = snaps.index(b)
        if i == 0:
            raise EichhornError(f"{b} is the first snapshot; nothing to compare with")
        a = snaps[i - 1]
    else:
        if len(snaps) < 2:
            raise EichhornError("diff needs at least two snapshots")
        a, b = snaps[-2], snaps[-1]
    d = diff(res, a, b)
    if args.json:
        _print_json(d)
        return EXIT_OK
    print(o.bold(f"{args.target}: {a} → {b}"))
    for item in d["added"]:
        print(o.green(f"  + {item['path']}") + o.dim(f"  {o.size(item['size'])}"))
    for item in d["changed"]:
        sizes = f"  {o.size(item['old_size'])} → {o.size(item['new_size'])}"
        print(o.yellow(f"  ~ {item['path']}") + o.dim(sizes))
    for item in d["removed"]:
        print(o.red(f"  - {item['path']}") + o.dim(f"  {o.size(item['size'])}"))
    print(
        o.dim(
            f"  {len(d['added'])} added, {len(d['changed'])} changed, {len(d['removed'])} removed"
        )
    )
    return EXIT_OK


def cmd_find(args: argparse.Namespace) -> int:
    from eichhorn.history import find

    cfg = _cfg(args)
    res = _resolver(cfg, args.target, args.repo)
    found = find(res, args.pattern)
    if args.json:
        _print_json(found)
        return EXIT_OK
    if not found:
        print(f"nothing matches {args.pattern!r} in {args.target}")
        return EXIT_FAILED
    for item in found[: args.limit]:
        print(o.bold(item["path"]))
        for i, v in enumerate(item["versions"], start=1):
            span = v["snapshots"][0]
            if len(v["snapshots"]) > 1:
                span += f" … {v['snapshots'][-1]}"
            print(
                f"  version {i}  {o.size(v['size']):>9}  {span}"
                + o.dim(f"  ({len(v['snapshots'])} snapshot(s))")
            )
    if len(found) > args.limit:
        print(o.dim(f"… {len(found) - args.limit} more; use --limit or a narrower pattern"))
    return EXIT_OK


def cmd_restore(args: argparse.Namespace) -> int:
    from eichhorn.restore import parse_destination, restore_local, restore_remote, select

    cfg = _cfg(args)
    res = _resolver(cfg, args.target, args.repo)
    snap = res.resolve_name(args.snapshot)
    items = select(res.index(snap), args.path)
    host, path = parse_destination(args.to)
    if host:
        result = restore_remote(res, snap, items, host, path, args.force, args.sudo)
    else:
        result = restore_local(res, snap, items, Path(path).expanduser(), args.force)
    if args.json:
        _print_json(result.as_dict())
    else:
        print(
            f"restored {result.files} file(s), {result.dirs} folder(s), {result.symlinks} "
            f"link(s), {o.size(result.bytes)} from {args.target} {snap} to {result.destination}"
        )
        for w in result.warnings:
            print(o.yellow(f"  ! {w}"))
    return EXIT_OK


def cmd_verify(args: argparse.Namespace) -> int:
    from eichhorn.content import Resolver
    from eichhorn.verify import against_source, verify, write_report

    cfg = _cfg(args)
    names = sorted(cfg.targets) if args.all else [args.target] if args.target else []
    if not names:
        raise EichhornError("name a target, or use --all")
    failed = 0
    reports = []
    with KeepAwake(enabled=cfg.defaults.keep_awake and not args.allow_sleep):
        for repo in _repos_for(cfg, args.repo):
            for name in names:
                cfg.target(name)
                res = Resolver(repo, name)
                if not res.snapshots:
                    continue
                if args.all_snapshots:
                    snaps = list(res.snapshots)
                else:
                    snaps = [res.resolve_name(args.snapshot or "latest")]
                rep = verify(res, snaps, sample=args.sample)
                if args.against_source:
                    rep.source = against_source(res, snaps[-1], cfg.target(name))
                write_report(res, rep)
                reports.append(rep.as_dict())
                failed += not rep.ok
                if not args.json:
                    _print_verify(name, repo.name, snaps, rep)
    if args.json:
        _print_json(reports)
    if not reports:
        raise EichhornError("no snapshots to verify in the connected repositories")
    return EXIT_FAILED if failed else EXIT_OK


def _print_verify(name: str, repo_name: str, snaps: list[str], rep: Any) -> None:
    state = o.green("ok") if rep.ok else o.red("PROBLEMS")
    which = snaps[0] if len(snaps) == 1 else f"{len(snaps)} snapshots"
    print(
        f"{o.bold(name)} in {repo_name} ({which}): {state}  "
        + o.dim(f"{rep.files} files, {o.size(rep.bytes)} read, {o.duration(rep.duration_s)}")
    )
    for c in rep.corrupted:
        good = f"; good copy in {', '.join(c['good_copies_in'])}" if c["good_copies_in"] else ""
        print(o.red(f"  corrupted  {c['path']} ({c['stored_in']}){good}"))
    for m in rep.missing:
        print(o.red(f"  missing    {m['path']} ({m['snapshot']})"))
    for u in rep.unexpected:
        print(o.yellow(f"  unexpected {u['path']} ({u['snapshot']})"))
    if rep.source:
        src = rep.source
        print(
            f"  against the source: {src['same']} same, {src['changed']} changed, "
            f"{src['new']} new, {src['gone']} gone since the snapshot"
        )
        for d in src["differs"]:
            print(o.red(f"  differs    {d['path']} (same size and time, other content)"))


def cmd_dump(args: argparse.Namespace) -> int:
    from eichhorn.dumps import rebuild

    cfg = _cfg(args)
    res = _resolver(cfg, args.target, args.repo)
    snap = res.resolve_name(args.snapshot)
    dumps = {d["name"]: d for d in res.repo.manifest(args.target, snap).get("dumps", [])}
    if args.name not in dumps or dumps[args.name].get("status") != "ok":
        known = ", ".join(n for n, d in dumps.items() if d.get("status") == "ok") or "none"
        raise EichhornError(f"no dump {args.name!r} in {args.target} {snap} (dumps: {known})")
    chunks = rebuild(res, snap, dumps[args.name])
    if args.to and args.to != "-":
        out = Path(args.to).expanduser()
        if out.exists() and not args.force:
            raise EichhornError(f"{out} exists; use --force to overwrite it")
        size = 0
        with open(out, "wb") as fh:
            for chunk in chunks:
                fh.write(chunk)
                size += len(chunk)
        if not args.quiet:
            print(f"wrote {o.size(size)} to {out}", file=sys.stderr)
    else:
        for chunk in chunks:
            sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
    return EXIT_OK


def cmd_serve(args: argparse.Namespace) -> int:
    from eichhorn.web import serve

    cfg = _cfg(args, strict=False)
    serve(args.config, args.listen or cfg.web_listen, open_browser=not args.no_browser)
    return EXIT_OK


def cmd_stats(args: argparse.Namespace) -> int:
    from eichhorn.stats import repo_stats, snapshot_stats, target_series

    cfg = _cfg(args)
    if args.target and args.snapshot:
        res = _resolver(cfg, args.target, args.repo)
        st = snapshot_stats(res, res.resolve_name(args.snapshot))
        if args.json:
            _print_json(st)
            return EXIT_OK
        print(
            o.bold(f"{st['target']} {st['snapshot']}")
            + f"  {o.status_color(st['status'])}"
            + o.dim(f"  in {st['repository']} ({st['storage']}, {st['engine']})")
        )
        speed = f"{o.size(st['speed_bps'])}/s" if st["speed_bps"] else "–"
        rows = [
            ["files", f"{st['files']} in {st['dirs']} folders"],
            ["size", o.size(st["logical_bytes"])],
            ["new data stored", o.size(st["stored_bytes"])],
            ["pruning it frees", o.size(st["freed_by_pruning"])],
            ["transferred", f"{o.size(st['transferred_bytes'])} at {speed}"],
            ["changes", changes_line(st["changes"])],
            ["total time", o.duration(st["timings"].get("total", 0))],
        ]
        for k, v in st["timings"].items():
            if k != "total" and v:
                rows.append([f"  {k}", o.duration(v)])
        print(o.table(rows, ["", ""]))
        if st["largest"]:
            print(o.bold("\nlargest files"))
            for f in st["largest"]:
                print(f"  {o.size(f['size']):>9}  {f['path']}")
        return EXIT_OK
    if args.target:
        res = _resolver(cfg, args.target, args.repo)
        series = target_series(res.repo, args.target)
        if args.json:
            _print_json(series)
            return EXIT_OK
        rows = [
            [
                s["snapshot"],
                o.status_color(s["status"]),
                str(s["files"]),
                o.size(s["logical_bytes"]),
                o.size(s["stored_bytes"]),
                o.size(s["transferred_bytes"]),
                changes_line(s),
                o.duration(s["duration_s"]),
            ]
            for s in series
        ]
        print(
            o.table(
                rows,
                ["SNAPSHOT", "STATUS", "FILES", "SIZE", "STORED", "TRANSFERRED", "CHANGES", "TIME"],
            )
        )
        return EXIT_OK
    out = []
    for repo in _repos_for(cfg, args.repo):
        st = repo_stats(repo, cfg)
        out.append(st)
        if args.json:
            continue
        print(
            o.bold(st["repository"])
            + o.dim(f"  {st['path']}  ({st['filesystem']}, {st['storage']})")
        )
        print(
            f"  used {o.size(st['used_bytes'])} of {o.size(st['total_bytes'])}, "
            f"{o.size(st['free_bytes'])} free"
        )
        if st["full_in_days"]:
            print(
                f"  grows about {o.size(st['growth_per_day'])} per day; full in about "
                f"{st['full_in_days']:.0f} days"
            )
        for t, used in sorted(st["used_by_targets"].items(), key=lambda x: -x[1]):
            print(f"  {o.size(used):>9}  {t}  ({st['snapshots'][t]} snapshot(s))")
    if args.json:
        _print_json(out)
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
    r = rsub.add_parser("sync", help="copy missing snapshots from one repository to another")
    r.add_argument("source", metavar="SRC")
    r.add_argument("destination", metavar="DST")
    r.add_argument("--target", action="append", metavar="T", help="only this target (repeatable)")
    r.add_argument("--both", action="store_true", help="sync in both directions")
    r.add_argument("--dry-run", action="store_true", help="only show what would be copied")
    r.add_argument("--no-prune", action="store_true", help="do not prune the destination")
    r.add_argument(
        "--include-older",
        action="store_true",
        help="also copy snapshots older than the destination's oldest",
    )
    r.set_defaults(func=cmd_repo_sync)
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
    r.add_argument("--recipe", help="start from a recipe (see `eichhorn recipe list`)")
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
        "--all-repos", action="store_true", help="every connected repository the target belongs to"
    )
    r.add_argument("--label", help="a note for the new snapshot")
    r.add_argument("--pin", action="store_true", help="never prune the new snapshot")
    r.add_argument("--dry-run", action="store_true", help="show what would be copied")
    r.add_argument(
        "--restart", action="store_true", help="throw away an unfinished run instead of resuming it"
    )
    r.add_argument("--no-prune", action="store_true", help="do not prune after the run")
    r.set_defaults(func=cmd_run)

    r = sub.add_parser("doctor", help="check targets' hosts, tools and databases")
    r.add_argument("targets", nargs="*", metavar="TARGET", help="default: all targets")
    r.set_defaults(func=cmd_doctor)

    rec = sub.add_parser("recipe", help="recipes for known apps")
    recsub = rec.add_subparsers(dest="recipe_command", metavar="ACTION", required=True)
    r = recsub.add_parser("list", help="list recipes and their parameters")
    r.set_defaults(func=cmd_recipe_list)

    r = sub.add_parser("prune", help="delete old snapshots according to the retention rules")
    r.add_argument("targets", nargs="*", metavar="TARGET", help="default: all targets")
    r.add_argument("--repo", metavar="NAME")
    r.add_argument("--dry-run", action="store_true", help="only show what would be deleted")
    r.set_defaults(func=cmd_prune)

    for name, text in (("pin", "never prune a snapshot"), ("unpin", "allow pruning again")):
        r = sub.add_parser(name, help=text)
        r.add_argument("target")
        r.add_argument("snapshot", help="snapshot name, a unique prefix of it, or latest")
        r.add_argument("--repo", metavar="NAME")
        if name == "pin":
            r.add_argument("--note", help="why it is pinned")
        r.set_defaults(func=cmd_pin)

    r = sub.add_parser("label", help="set or remove a snapshot's label")
    r.add_argument("target")
    r.add_argument("snapshot", help="snapshot name, a unique prefix of it, or latest")
    r.add_argument("text", help="the label; an empty text removes it")
    r.add_argument("--repo", metavar="NAME")
    r.set_defaults(func=cmd_label)

    r = sub.add_parser("diff", help="what changed between two snapshots")
    r.add_argument("target")
    r.add_argument("snap_a", nargs="?", metavar="SNAP_A", help="default: the second newest")
    r.add_argument("snap_b", nargs="?", metavar="SNAP_B", help="default: the newest")
    r.add_argument("--repo", metavar="NAME")
    r.set_defaults(func=cmd_diff)

    r = sub.add_parser("find", help="find a file in all snapshots and show its versions")
    r.add_argument("target")
    r.add_argument("pattern", help="a name or glob, e.g. report.docx, '*.jpg', 'docs/*/a.txt'")
    r.add_argument("--repo", metavar="NAME")
    r.add_argument("--limit", type=int, default=50, help="show at most N files")
    r.set_defaults(func=cmd_find)

    r = sub.add_parser("restore", help="copy files out of a snapshot")
    r.add_argument("target")
    r.add_argument("snapshot", help="snapshot name, a unique prefix of it, or latest")
    r.add_argument("path", nargs="?", help="file or folder in the snapshot (default: everything)")
    r.add_argument(
        "--to",
        required=True,
        metavar="DEST",
        help="a local folder, or host:/path to restore onto a server",
    )
    r.add_argument("--force", action="store_true", help="overwrite existing files")
    r.add_argument("--sudo", action="store_true", help="unpack with sudo on the server")
    r.add_argument("--repo", metavar="NAME")
    r.set_defaults(func=cmd_restore)

    r = sub.add_parser("dump", help="write a database dump from a snapshot, uncompressed")
    r.add_argument("target")
    r.add_argument("snapshot", help="snapshot name, a unique prefix of it, or latest")
    r.add_argument("name", help="the dump's name in the target config")
    r.add_argument("--to", metavar="FILE", help="write to FILE (default: standard output)")
    r.add_argument("--force", action="store_true", help="overwrite FILE")
    r.add_argument("--repo", metavar="NAME")
    r.set_defaults(func=cmd_dump)

    r = sub.add_parser("verify", help="check snapshots against their checksums")
    r.add_argument("target", nargs="?")
    r.add_argument("snapshot", nargs="?", help="default: latest")
    r.add_argument("--all", action="store_true", help="all targets")
    r.add_argument("--all-snapshots", action="store_true", help="every snapshot")
    r.add_argument("--sample", type=float, metavar="PCT", help="check a random PCT %% of files")
    r.add_argument(
        "--against-source", action="store_true", help="also compare the snapshot with the host"
    )
    r.add_argument("--repo", metavar="NAME")
    r.set_defaults(func=cmd_verify)

    r = sub.add_parser("stats", help="statistics of a repository, a target or a snapshot")
    r.add_argument("target", nargs="?")
    r.add_argument("snapshot", nargs="?")
    r.add_argument("--repo", metavar="NAME")
    r.set_defaults(func=cmd_stats)

    r = sub.add_parser("serve", help="start the web interface")
    r.add_argument("--listen", metavar="ADDR", help="address (default: 127.0.0.1:8765)")
    r.add_argument("--no-browser", action="store_true", help="do not open a browser")
    r.set_defaults(func=cmd_serve)

    r = sub.add_parser("status", help="last backup of each target in each repository")
    r.set_defaults(func=cmd_status)

    r = sub.add_parser("snapshots", help="list the snapshots of a target")
    r.add_argument("target")
    r.add_argument("--repo", metavar="NAME")
    r.set_defaults(func=cmd_snapshots)
    return p


def use_utf8_output() -> None:
    """Write UTF-8 even where Python would pick a legacy code page.

    On Windows, output that goes to a pipe or a file (not the console) uses the
    ANSI code page (e.g. cp1252), which cannot encode characters such as → or ….
    """
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if encoding != "utf8" and hasattr(stream, "reconfigure"):
            with contextlib.suppress(ValueError, OSError):
                stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    use_utf8_output()
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

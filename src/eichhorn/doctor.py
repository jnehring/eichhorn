"""`eichhorn doctor`: check a target's host without changing anything (spec 6)."""

from __future__ import annotations

import shlex
import sys
from dataclasses import dataclass
from typing import Any

from eichhorn.config import Target
from eichhorn.connections import Command, Connection, SshConnection, connect, run
from eichhorn.dumps import build_command
from eichhorn.engines import rsync_blockers
from eichhorn.stores.base import StoreCapabilities
from eichhorn.tools import local_rsync_note

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass
class Check:
    name: str
    state: str
    detail: str = ""


def _long_paths_enabled() -> bool | None:
    if sys.platform != "win32":
        return None
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\FileSystem",
        )
        value, _ = winreg.QueryValueEx(key, "LongPathsEnabled")
        return bool(value)
    except OSError:
        return False


def local_checks() -> list[Check]:
    checks = [
        Check(
            "local rsync",
            OK if "needed" not in local_rsync_note() else WARN,
            local_rsync_note() + " (only needed for the rsync engine)",
        )
    ]
    lp = _long_paths_enabled()
    if lp is False:
        checks.append(
            Check(
                "Windows long paths",
                WARN,
                "LongPathsEnabled is off: Explorer may not open deep paths in backups",
            )
        )
    return checks


def _db_check_command(dump: dict[str, Any], sudo: bool) -> Command | None:
    kind = dump["type"]
    db = dump.get("database")
    user = dump.get("user")
    pw = dump.get("password_env")
    if kind == "postgres":
        argv = ["psql", "-X", "-At", "-c", "select 1"]
        if user:
            argv += ["-U", user]
        argv += ["-d", "postgres" if db in (None, "*") else db]
        prefix = f'PGPASSWORD="${pw}" ' if pw else ""
    elif kind == "mysql":
        from eichhorn.dumps import _mysql_tool

        args = ["-N", "-e", "select 1"]
        if user:
            args += ["-u", user]
        if db and db != "*":
            args.append(db)
        return _mysql_tool(dump, "mariadb", "mysql", args, sudo)
    else:
        return None
    if dump.get("container"):
        inner = ["sh", "-c", prefix + shlex.join(argv)] if prefix else argv
        return Command(argv=["docker", "exec", dump["container"], *inner], sudo=sudo)
    return Command(shell=prefix + shlex.join(argv), sudo=sudo)


def check_target(target: Target, caps: StoreCapabilities | None) -> list[Check]:
    checks: list[Check] = []
    conn: Connection = connect(target.host, sudo=target.sudo, shell=target.shell)

    if isinstance(conn, SshConnection):
        res = run(conn, Command(argv=["true"]), timeout=60)
        if not res.ok:
            checks.append(Check("SSH login", FAIL, res.err_text(500) or "cannot connect"))
            return checks
        info = conn.info
        checks.append(Check("SSH login", OK, f"{info.user}@{target.host} ({info.system})"))
        if info.gnu_find:
            checks.append(Check("file listing", OK, "GNU find"))
        elif info.python3:
            checks.append(Check("file listing", OK, "python3 (no GNU find)"))
        else:
            checks.append(Check("file listing", FAIL, "needs GNU find or python3 on the host"))
        checks.append(
            Check(
                "tar",
                OK if info.tar else FAIL,
                info.paths.get("tar", "missing: needed by the builtin engine"),
            )
        )
        if info.rsync_version:
            checks.append(
                Check(
                    "rsync on host",
                    OK if info.rsync_ok else WARN,
                    ".".join(map(str, info.rsync_version)),
                )
            )
        else:
            checks.append(
                Check("rsync on host", WARN, "not installed (the builtin engine is used instead)")
            )

    if target.sudo:
        res = run(conn, Command(argv=["true"], sudo=True), timeout=60)
        if res.ok:
            checks.append(Check("sudo", OK, "works without a password"))
        else:
            checks.append(
                Check("sudo", FAIL, f"sudo -n failed: {res.err_text(300)}. " + sudoers_hint(conn))
            )

    for s in target.sources:
        ok = conn.exists(s.path)
        checks.append(
            Check(
                f"source {s.name}",
                OK if ok else FAIL,
                s.path if ok else f"{s.path} not found or not readable",
            )
        )

    for d in target.dumps:
        container = d.get("container")
        if container:
            res = run(
                conn,
                Command(
                    argv=["docker", "inspect", "-f", "{{.State.Running}}", container],
                    sudo=target.sudo,
                ),
                timeout=60,
            )
            running = res.ok and res.stdout.strip() == b"true"
            why = "" if running else ": " + (res.err_text(200) or "not running")
            checks.append(
                Check(f"dump {d['name']}: container", OK if running else FAIL, container + why)
            )
            if not running:
                continue
        cmd = _db_check_command(d, target.sudo)
        if cmd is not None:
            res = run(conn, cmd, timeout=60)
            checks.append(
                Check(
                    f"dump {d['name']}: database",
                    OK if res.ok else FAIL,
                    "reachable" if res.ok else res.err_text(300),
                )
            )
        checks.append(
            Check(f"dump {d['name']}: command", OK, build_command(d, target.sudo).describe())
        )

    for phase, cmds in target.hooks.items():
        for c in cmds:
            checks.append(Check(f"hook {phase}", OK, c + "  (not run by doctor)"))

    if caps is not None:
        blockers = rsync_blockers(caps, conn)
        engine = target.engine
        if engine == "auto":
            engine = "builtin" if blockers else "rsync"
            detail = f"auto → {engine}" + (f" ({'; '.join(blockers)})" if blockers else "")
            checks.append(Check("engine", OK, detail))
        elif engine == "rsync" and blockers:
            checks.append(Check("engine", FAIL, "rsync not possible: " + "; ".join(blockers)))
        else:
            checks.append(Check("engine", OK, engine))
    return checks


def sudoers_hint(conn: Connection) -> str:
    if not isinstance(conn, SshConnection):
        return ""
    info = conn.info
    tools = [
        info.paths[t]
        for t in ("rsync", "tar", "find", "python3", "docker", "cat", "tail")
        if t in info.paths
    ]
    return (
        "Add with `sudo visudo -f /etc/sudoers.d/eichhorn`: "
        f"{info.user} ALL=(root) NOPASSWD: {', '.join(tools)}"
        " (hooks with sudo also need /bin/sh)"
    )

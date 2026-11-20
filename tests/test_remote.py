"""Milestone 2: SSH hosts (via a fake ssh), engines, dumps, hooks, recipes, doctor."""

from __future__ import annotations

import gzip
import json
import os
import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from eichhorn.connections import Command, LocalConnection, SshConnection
from eichhorn.dumps import build_command
from eichhorn.engines.common import Previous
from eichhorn.excludes import Excludes
from eichhorn.tools import local_rsync

Cli = Callable[..., int]

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell")


@pytest.fixture(autouse=True)
def fake_ssh(monkeypatch: pytest.MonkeyPatch) -> None:
    shim = Path(__file__).with_name("fake_ssh.py")
    monkeypatch.setenv("EICHHORN_SSH", f"{shlex.quote(sys.executable)} {shlex.quote(str(shim))}")


@pytest.fixture
def server(tmp_path: Path) -> Path:
    """A folder that plays the server's data."""
    root = tmp_path / "server"
    (root / "app" / "sub").mkdir(parents=True)
    (root / "app" / "a.txt").write_text("alpha")
    (root / "app" / "sub" / "b.txt").write_text("beta")
    (root / "app" / "with space.txt").write_text("space")
    (root / "app" / "cache").mkdir()
    (root / "app" / "cache" / "x.tmp").write_text("x")
    os.symlink("a.txt", root / "app" / "link")
    return root


def make_target(tmp_path: Path, cli: Cli, body: str, name: str = "srv") -> Path:
    repo = tmp_path / "drive" / "eichhorn"
    if not (repo / "repository.yaml").exists():
        assert cli("repo", "init", str(repo), "--name", "Backup-A") == 0
    targets = tmp_path / "cfg" / "targets"
    targets.mkdir(parents=True, exist_ok=True)
    (targets / f"{name}.yaml").write_text(body)
    return repo


def snaps(repo: Path, target: str = "srv") -> list[Path]:
    d = repo / "targets" / target / "snapshots"
    return sorted(d.iterdir()) if d.exists() else []


def manifest(snap: Path) -> dict:  # type: ignore[type-arg]
    return json.loads((snap / "snapshot.json").read_text())


# ---------------------------------------------------------------- connection
def test_ssh_listing_matches_local(server: Path) -> None:
    ex = Excludes(["/cache/"])
    remote = SshConnection("fakehost").listing(str(server / "app"), ex)
    local = LocalConnection().listing(str(server / "app"), ex)
    assert [(e.rel, e.type, e.size) for e in remote.entries] == [
        (e.rel, e.type, e.size) for e in local.entries
    ]
    for r, loc in zip(remote.entries, local.entries, strict=True):
        assert abs(r.mtime_ns - loc.mtime_ns) < 1000
        assert r.mode == loc.mode
    link = next(e for e in remote.entries if e.rel == "link")
    assert link.type == "symlink" and link.target == "a.txt"


def test_python_lister_fallback(server: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = SshConnection("fakehost")
    info = conn.info
    info.gnu_find = False
    assert info.python3
    listing = conn.listing(str(server / "app"), Excludes([]))
    assert {e.rel for e in listing.entries} >= {"a.txt", "sub/b.txt", "with space.txt", "link"}


def test_ssh_read_many_and_offset(server: Path) -> None:
    conn = SshConnection("fakehost")
    got = {
        f.rel: f.stream.read()
        for f in conn.read_many(str(server / "app"), ["a.txt", "with space.txt", "missing"])
        if f.stream
    }
    assert got == {"a.txt": b"alpha", "with space.txt": b"space"}
    with conn.open_read(str(server / "app"), "a.txt", offset=2) as r:
        assert r.read() == b"pha"
    with pytest.raises(FileNotFoundError), conn.open_read(str(server / "app"), "nope") as r:
        r.read()


def test_remote_command_quoting() -> None:
    conn = SshConnection("h")
    assert conn.remote_string(Command(argv=["ls", "a b"])) == "ls 'a b'"
    assert conn.remote_string(Command(argv=["ls"], sudo=True)) == "sudo -n ls"
    assert conn.remote_string(Command(shell="echo $HOME && true", sudo=True)) == (
        "sudo -n sh -c 'echo $HOME && true'"
    )


# ---------------------------------------------------------------- runs over ssh
def test_backup_over_ssh(tmp_path: Path, cli: Cli, server: Path) -> None:
    repo = make_target(
        tmp_path,
        cli,
        f"host: fakehost\nengine: builtin\nsources:\n"
        f"  - name: app\n    path: '{server / 'app'}'\n    exclude: [/cache/]\n",
    )
    assert cli("run", "srv") == 0
    (server / "app" / "new.txt").write_text("new")
    assert cli("run", "srv") == 0
    first, second = snaps(repo)
    assert (second / "files/app/with space.txt").read_text() == "space"
    assert not (second / "files/app/cache").exists()
    assert (first / "files/app/a.txt").stat().st_ino == (second / "files/app/a.txt").stat().st_ino
    ch = manifest(second)["sources"][0]["changes"]
    assert (ch["new"], ch["unchanged"]) == (1, 3)
    assert manifest(second)["engine"] == "builtin"


def test_large_file_over_ssh_resumes(tmp_path: Path, server: Path) -> None:
    from eichhorn.config import Source
    from eichhorn.engines import builtin
    from eichhorn.stores.base import StoreCapabilities

    data = os.urandom(200_000)
    (server / "app" / "big.bin").write_bytes(data)
    dest = tmp_path / "dest"
    (dest / "files/app").mkdir(parents=True)
    conn = SshConnection("fakehost")
    listed = next(
        e for e in conn.listing(str(server / "app"), Excludes([])).entries if e.rel == "big.bin"
    )
    partial = dest / "files/app/big.bin.eichhorn-partial"
    partial.write_bytes(data[:50_000])
    partial.with_name(partial.name + ".src").write_text(f"{listed.size} {listed.mtime_ns}")
    old = builtin.RESUME_THRESHOLD
    builtin.RESUME_THRESHOLD = 1000
    try:
        eng = builtin.BuiltinEngine(StoreCapabilities(True, True, True, None))
        _, stats = eng.transfer(
            conn, Source("app", str(server / "app"), ["/cache/"]), dest, Previous(None, {})
        )
    finally:
        builtin.RESUME_THRESHOLD = old
    assert (dest / "files/app/big.bin").read_bytes() == data
    big = 200_000 - 50_000  # only the rest was read, with tail -c +N
    assert stats.transferred_bytes == big + sum(
        len(p.read_bytes()) for p in (server / "app").rglob("*.txt") if "cache" not in str(p)
    )


@pytest.mark.skipif(local_rsync() is None, reason="needs rsync >= 3.1")
def test_rsync_engine_over_ssh(tmp_path: Path, cli: Cli, server: Path) -> None:
    repo = make_target(
        tmp_path,
        cli,
        f"host: fakehost\nengine: rsync\nsources:\n"
        f"  - name: app\n    path: '{server / 'app'}'\n    exclude: [/cache/]\n",
    )
    assert cli("run", "srv") == 0
    (server / "app" / "a.txt").write_text("alpha 2")
    st = (server / "app" / "a.txt").stat()
    os.utime(server / "app" / "a.txt", ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
    assert cli("run", "srv") == 0
    first, second = snaps(repo)
    m = manifest(second)
    assert m["engine"] == "rsync"
    assert (second / "files/app/a.txt").read_text() == "alpha 2"
    sub1, sub2 = first / "files/app/sub/b.txt", second / "files/app/sub/b.txt"
    assert sub1.stat().st_ino == sub2.stat().st_ino
    assert m["sources"][0]["changes"]["changed"] == 1


def test_unreachable_host_fails_cleanly(tmp_path: Path, cli: Cli) -> None:
    repo = make_target(tmp_path, cli, "host: unreachable\nsources:\n  - {name: a, path: /srv}\n")
    assert cli("run", "srv") == 1
    assert snaps(repo) == []


# ---------------------------------------------------------------- hooks
def test_hooks_order_and_post_always(tmp_path: Path, cli: Cli, server: Path) -> None:
    log = tmp_path / "hooks.log"
    repo = make_target(
        tmp_path,
        cli,
        f"""host: fakehost
sources:
  - name: app
    path: '{server / "app"}'
hooks:
  pre: ["echo pre >> '{log}'"]
  post: ["echo post >> '{log}'"]
  post_always: ["echo always >> '{log}'"]
""",
    )
    assert cli("run", "srv") == 0
    assert log.read_text().split() == ["pre", "post", "always"]
    m = manifest(snaps(repo)[0])
    assert [h["phase"] for h in m["hooks"]] == ["pre", "post", "post_always"]


def test_failing_pre_hook_stops_but_runs_post_always(
    tmp_path: Path, cli: Cli, server: Path
) -> None:
    log = tmp_path / "hooks.log"
    repo = make_target(
        tmp_path,
        cli,
        f"""host: fakehost
sources:
  - name: app
    path: '{server / "app"}'
hooks:
  pre: ["echo pre >> '{log}'; exit 3"]
  post_always: ["echo always >> '{log}'"]
""",
    )
    assert cli("run", "srv") == 1
    assert log.read_text().split() == ["pre", "always"]
    assert snaps(repo) == []
    assert list((repo / "targets" / "srv").glob(".incomplete-*")) == []


# ---------------------------------------------------------------- dumps
def test_command_dump(tmp_path: Path, cli: Cli, server: Path) -> None:
    repo = make_target(
        tmp_path,
        cli,
        f"""host: fakehost
sources:
  - name: app
    path: '{server / "app"}'
dumps:
  - name: db
    type: command
    run: printf 'CREATE TABLE x;'
    ext: sql
""",
    )
    assert cli("run", "srv") == 0
    snap = snaps(repo)[0]
    assert gzip.decompress((snap / "dumps/db.sql.gz").read_bytes()) == b"CREATE TABLE x;"
    assert "dumps/db.sql.gz" in (snap / "checksums.sha256").read_text()
    assert manifest(snap)["dumps"][0]["status"] == "ok"


def test_failed_dump_makes_partial_snapshot(tmp_path: Path, cli: Cli, server: Path) -> None:
    repo = make_target(
        tmp_path,
        cli,
        f"""host: fakehost
sources:
  - name: app
    path: '{server / "app"}'
dumps:
  - name: db
    type: command
    run: echo oops >&2; exit 1
""",
    )
    assert cli("run", "srv") == 1
    m = manifest(snaps(repo)[0])
    assert m["status"] == "partial"
    assert "oops" in m["dumps"][0]["error"]
    assert not (snaps(repo)[0] / "dumps/db.dump.gz").exists()


def test_dump_commands() -> None:
    pg = build_command(
        {"name": "n", "type": "postgres", "container": "pg", "database": "nc", "user": "u"}
    )
    assert pg.argv == ["docker", "exec", "pg", "pg_dump", "-U", "u", "--clean", "--if-exists", "nc"]
    allpg = build_command({"name": "n", "type": "postgres", "database": "*", "user": "postgres"})
    assert allpg.argv == ["pg_dumpall", "-U", "postgres", "--clean", "--if-exists"]
    my = build_command(
        {
            "name": "n",
            "type": "mysql",
            "container": "db",
            "database": "nc",
            "user": "nc",
            "password_env": "MYSQL_PASSWORD",
        },
        sudo=True,
    )
    assert my.sudo
    assert my.argv is not None and my.argv[:4] == ["docker", "exec", "db", "sh"]
    script = my.argv[5]
    assert script.startswith('MYSQL_PWD="$MYSQL_PASSWORD"; export MYSQL_PWD;')
    assert "command -v mariadb-dump || command -v mysqldump" in script
    assert script.endswith("-u nc --single-transaction --routines --triggers --databases nc")


def test_mysql_dump_falls_back_to_the_available_tool(tmp_path: Path) -> None:
    """MariaDB 11+ has only mariadb-dump; MySQL has only mysqldump."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "mysqldump"
    fake.write_text('#!/bin/sh\necho "dump by mysqldump with $MYSQL_PWD: $*"\n')
    fake.chmod(0o755)
    cmd = build_command(
        {"name": "n", "type": "mysql", "database": "db", "user": "u", "password_env": "SECRET"}
    )
    env = {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin", "SECRET": "pw"}
    out = subprocess.run(["sh", "-c", cmd.shell or ""], env=env, capture_output=True, text=True)
    assert out.stdout.startswith("dump by mysqldump with pw: -u u --single-transaction")
    (bin_dir / "mariadb-dump").write_text('#!/bin/sh\necho "dump by mariadb-dump"\n')
    (bin_dir / "mariadb-dump").chmod(0o755)
    out = subprocess.run(["sh", "-c", cmd.shell or ""], env=env, capture_output=True, text=True)
    assert out.stdout.strip() == "dump by mariadb-dump"


# ---------------------------------------------------------------- recipes
RECIPE_CASES = {
    "immich": {"compose_dir": "/srv/immich", "upload_location": "/srv/immich/library"},
    "nextcloud": {
        "compose_dir": "/srv/nc",
        "data_dir": "/srv/nc/html/data",
        "app_container": "nc-app",
        "db_container": "nc-db",
    },
    "affine": {
        "compose_dir": "/srv/affine",
        "upload_location": "/srv/affine/storage",
        "config_location": "/srv/affine/config",
    },
    "compose-postgres": {
        "compose_dir": "/srv/didgelab",
        "db_container": "didgelab-db",
        "db_name": "didgelab",
        "data_dirs": ["/srv/didgelab/uploads"],
    },
    "caddy": {"compose_dir": "/srv/caddy", "data_dir": "/srv/caddy/data"},
    "static-site": {"path": "/srv/sites/example.org"},
}


@pytest.mark.parametrize("recipe", sorted(RECIPE_CASES))
def test_recipes_expand_to_valid_targets(
    tmp_path: Path, cli: Cli, recipe: str, capsys: pytest.CaptureFixture[str]
) -> None:
    import yaml

    params = yaml.safe_dump({"with": RECIPE_CASES[recipe]})
    make_target(tmp_path, cli, f"host: web1\nrecipe: {recipe}\n{params}", name="t")
    capsys.readouterr()
    assert cli("config", "check") == 0, capsys.readouterr().out
    assert cli("target", "show", "t", "--expanded") == 0
    data = yaml.safe_load(capsys.readouterr().out)
    assert data["host"] == "web1"
    assert data["sources"]


def test_recipe_details(tmp_path: Path, cli: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    import yaml

    from eichhorn import recipes

    immich = recipes.expand(recipes.load("immich"), RECIPE_CASES["immich"])
    compose = next(s for s in immich["sources"] if s["name"] == "compose")
    assert "/library/" in compose["exclude"]  # nested source excluded, not copied twice
    library = next(s for s in immich["sources"] if s["name"] == "library")
    assert "/thumbs/" in library["exclude"]
    assert not immich["hooks"]["pre"]  # stop_server is off by default
    cp = recipes.expand(recipes.load("compose-postgres"), RECIPE_CASES["compose-postgres"])
    assert [s["name"] for s in cp["sources"]] == ["compose", "data-1"]

    own = {
        "recipe": "immich",
        "sources": [{"name": "extra", "path": "/x"}],
        "hooks": {"pre": ["echo hi"]},
    }
    merged = recipes.merge(immich, own)
    assert [s["name"] for s in merged["sources"]] == ["library", "compose", "extra"]
    assert merged["hooks"]["pre"] == ["echo hi"]

    make_target(tmp_path, cli, "host: web1\nrecipe: immich\nwith:\n  compose_dirr: /x\n", "bad")
    capsys.readouterr()
    assert cli("config", "check") == 2
    out = capsys.readouterr().out
    assert "no parameter 'compose_dirr'" in out
    assert yaml is not None


def test_recipe_list_and_new(tmp_path: Path, cli: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli("recipe", "list") == 0
    out = capsys.readouterr().out
    for name in RECIPE_CASES:
        assert name in out
    assert cli("target", "new", "photos", "--recipe", "immich") == 0
    text = (tmp_path / "cfg" / "targets" / "photos.yaml").read_text()
    assert "recipe: immich" in text and "upload_location" in text


# ---------------------------------------------------------------- doctor
def test_doctor(tmp_path: Path, cli: Cli, server: Path, capsys: pytest.CaptureFixture[str]) -> None:
    make_target(
        tmp_path,
        cli,
        f"host: fakehost\nsources:\n  - name: app\n    path: '{server / 'app'}'\n"
        "  - name: gone\n    path: /does/not/exist\n",
    )
    capsys.readouterr()
    assert cli("doctor", "srv") == 1  # the missing source fails
    out = capsys.readouterr().out
    assert "SSH login" in out and "source app" in out and "/does/not/exist not found" in out
    assert cli("--json", "doctor", "srv") == 1
    data = json.loads(capsys.readouterr().out)
    states = {c["name"]: c["state"] for c in data["targets"]["srv"]}
    assert states["source app"] == "ok" and states["source gone"] == "fail"


def test_own_source_keeps_nested_exclusion() -> None:
    """Replacing a recipe's source (to add excludes) must not back up a nested one twice."""
    from eichhorn import recipes

    immich = recipes.expand(recipes.load("immich"), RECIPE_CASES["immich"])
    own = {"sources": [{"name": "compose", "path": "/srv/immich", "exclude": ["/postgres/"]}]}
    compose = next(s for s in recipes.merge(immich, own)["sources"] if s["name"] == "compose")
    assert compose["exclude"] == ["/postgres/", "/library/"]

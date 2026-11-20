"""Milestone 5: delta dumps (spec 5.8)."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from eichhorn.stores.localfs import LocalRepository

Cli = Callable[..., int]

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="uses cat in a shell command")


def sql(n: int, change: str = "") -> bytes:
    rows = [f"INSERT INTO t VALUES ({i}, 'row {i}');" for i in range(n)]
    if change:
        rows[n // 2] = change
    return ("\n".join(rows) + "\n").encode()


def setup(
    tmp_path: Path, cli: Cli, storage: str | None = None, extra: str = ""
) -> tuple[Path, Path]:
    data = tmp_path / "db.sql"
    data.write_bytes(sql(20000))
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "x.txt").write_text("x")
    repo = tmp_path / "drive" / "eichhorn"
    args = ["repo", "init", str(repo), "--name", "A"]
    if storage:
        args += ["--storage", storage]
    assert cli(*args) == 0
    t = tmp_path / "cfg" / "targets"
    t.mkdir(parents=True)
    (t / "app.yaml").write_text(
        f"""sources:
  - name: files
    path: '{tmp_path / "files"}'
dumps:
  - name: db
    type: command
    ext: sql
    run: cat {shlex.quote(str(data))}
    delta:
      full_every: 3
      max_ratio: 0.5
{extra}"""
    )
    return data, repo


def snaps(repo: Path) -> list[str]:
    return LocalRepository.open(repo).snapshots("app")


def dump_info(repo: Path, snap: str) -> dict:  # type: ignore[type-arg]
    m = json.loads((repo / "targets/app/snapshots" / snap / "snapshot.json").read_text())
    return m["dumps"][0]  # type: ignore[no-any-return]


def test_full_patch_patch_full(tmp_path: Path, cli: Cli) -> None:
    data, repo = setup(tmp_path, cli)
    versions = []
    for i in range(4):
        data.write_bytes(sql(20000, f"-- change {i}"))
        versions.append(data.read_bytes())
        assert cli("run", "app") == 0
        if i < 3:
            import time

            time.sleep(1.05)  # distinct snapshot names
    names = snaps(repo)
    modes = [dump_info(repo, s)["delta"]["mode"] for s in names]
    assert modes == ["full", "patch", "patch", "full"]
    second = repo / "targets/app/snapshots" / names[1] / "dumps"
    first = repo / "targets/app/snapshots" / names[0] / "dumps"
    assert (second / "db.base.sql.zst").stat().st_ino == (first / "db.base.sql.zst").stat().st_ino
    patch = (second / "db.patch.zst").stat().st_size
    assert patch < (first / "db.base.sql.zst").stat().st_size / 5
    for snap, expected in zip(names, versions, strict=True):
        out = tmp_path / f"out-{snap}.sql"
        assert cli("dump", "app", snap, "db", "--to", str(out)) == 0
        assert out.read_bytes() == expected
    assert cli("verify", "app", "--all-snapshots") == 0


def test_corrupt_patch_is_found(tmp_path: Path, cli: Cli) -> None:
    data, repo = setup(tmp_path, cli)
    assert cli("run", "app") == 0
    import time

    time.sleep(1.05)
    data.write_bytes(sql(20000, "-- changed"))
    assert cli("run", "app") == 0
    patch = repo / "targets/app/snapshots" / snaps(repo)[-1] / "dumps/db.patch.zst"
    raw = bytearray(patch.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    patch.write_bytes(bytes(raw))
    assert cli("verify", "app") == 1


def test_big_change_stores_a_full_dump(tmp_path: Path, cli: Cli) -> None:
    data, repo = setup(tmp_path, cli)
    assert cli("run", "app") == 0
    import os
    import time

    time.sleep(1.05)
    data.write_bytes(os.urandom(300_000))  # nothing in common with the base
    assert cli("run", "app") == 0
    info = dump_info(repo, snaps(repo)[-1])
    assert info["delta"]["mode"] == "full"
    assert "patch over" in info["delta"]["reason"]


def test_sparse_delta_and_prune(tmp_path: Path, cli: Cli) -> None:
    data, repo = setup(tmp_path, cli, storage="sparse", extra="retention:\n  keep_last: 1\n")
    assert cli("run", "app", "--no-prune") == 0
    import time

    time.sleep(1.05)
    data.write_bytes(sql(20000, "-- v2"))
    expected = data.read_bytes()
    assert cli("run", "app", "--no-prune") == 0
    last = snaps(repo)[-1]
    assert not (repo / "targets/app/snapshots" / last / "dumps/db.base.sql.zst").exists()
    assert cli("prune") == 0
    assert snaps(repo) == [last]
    assert (repo / "targets/app/snapshots" / last / "dumps/db.base.sql.zst").exists()  # moved
    out = tmp_path / "out.sql"
    assert cli("dump", "app", "latest", "db", "--to", str(out)) == 0
    assert out.read_bytes() == expected
    assert cli("verify", "app") == 0


@pytest.mark.skipif(shutil.which("zstd") is None, reason="needs the zstd command")
def test_patch_works_with_zstd_command(tmp_path: Path, cli: Cli) -> None:
    data, repo = setup(tmp_path, cli)
    assert cli("run", "app") == 0
    import time

    time.sleep(1.05)
    data.write_bytes(sql(20000, "-- v2"))
    assert cli("run", "app") == 0
    d = repo / "targets/app/snapshots" / snaps(repo)[-1] / "dumps"
    base = tmp_path / "base.sql"
    subprocess.run(["zstd", "-q", "-d", str(d / "db.base.sql.zst"), "-o", str(base)], check=True)
    out = tmp_path / "rebuilt.sql"
    subprocess.run(
        [
            "zstd",
            "-q",
            "-d",
            "--long=31",
            f"--patch-from={base}",
            str(d / "db.patch.zst"),
            "-o",
            str(out),
        ],
        check=True,
    )
    assert out.read_bytes() == data.read_bytes()

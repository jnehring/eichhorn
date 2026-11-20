from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from eichhorn.config import Source
from eichhorn.connections.local import LocalConnection
from eichhorn.engines.builtin import BuiltinEngine, Previous
from eichhorn.index import load_index
from eichhorn.lock import LOCK_NAME
from eichhorn.stores.base import StoreCapabilities

from .conftest import touch_later

Setup = Callable[..., tuple[Path, Path]]
Cli = Callable[..., int]


def snapshots(repo: Path) -> list[Path]:
    d = repo / "targets" / "docs" / "snapshots"
    return sorted(p for p in d.iterdir()) if d.exists() else []


def manifest(snap: Path) -> dict:  # type: ignore[type-arg]
    return json.loads((snap / "snapshot.json").read_text())


def test_first_and_second_run(setup: Setup, cli: Cli) -> None:
    src, repo = setup()
    assert cli("run", "docs") == 0
    touch_later(src / "a.txt", "alpha changed")
    (src / "c.txt").write_text("gamma")
    (src / "sub" / "b.txt").unlink()
    assert cli("run", "docs") == 0

    first, second = snapshots(repo)
    assert (first / "files/docs/sub/b.txt").read_text() == "beta"
    assert not (second / "files/docs/sub/b.txt").exists()
    assert (second / "files/docs/a.txt").read_text() == "alpha changed"
    assert (first / "files/docs/a.txt").read_text() == "alpha"

    ch = manifest(second)["sources"][0]["changes"]
    assert (ch["new"], ch["changed"], ch["deleted"], ch["unchanged"]) == (1, 1, 1, 0)
    assert (repo / "targets/docs/latest").read_text().strip() == second.name


def test_unchanged_files_are_hardlinked(setup: Setup, cli: Cli) -> None:
    src, repo = setup()
    assert cli("run", "docs") == 0
    (src / "new.txt").write_text("new")
    assert cli("run", "docs") == 0
    first, second = snapshots(repo)
    a1, a2 = first / "files/docs/a.txt", second / "files/docs/a.txt"
    assert a1.stat().st_ino == a2.stat().st_ino
    assert a2.stat().st_nlink == 2
    assert manifest(second)["sources"][0]["changes"]["unchanged"] == 2


def test_sparse_storage_keeps_only_changes(setup: Setup, cli: Cli) -> None:
    src, repo = setup(storage="sparse")
    assert cli("run", "docs") == 0
    touch_later(src / "a.txt", "alpha 2")
    assert cli("run", "docs") == 0
    first, second = snapshots(repo)
    assert (first / "files/docs/sub/b.txt").exists()
    assert not (second / "files/docs/sub/b.txt").exists()  # unchanged: not stored again
    assert (second / "files/docs/a.txt").read_text() == "alpha 2"
    idx = load_index(second)
    assert idx["files/docs/sub/b.txt"].here is False
    assert idx["files/docs/sub/b.txt"].sha256 is not None
    assert idx["files/docs/a.txt"].here is True


def test_checksums_file_is_standard(setup: Setup, cli: Cli) -> None:
    _, repo = setup()
    assert cli("run", "docs") == 0
    (snap,) = snapshots(repo)
    lines = (snap / "checksums.sha256").read_text().splitlines()
    assert len(lines) == 2
    tool = (
        ["sha256sum", "-c"] if sys.platform.startswith("linux") else ["shasum", "-a", "256", "-c"]
    )
    if sys.platform != "win32":
        subprocess.run([*tool, "checksums.sha256"], cwd=snap, check=True, capture_output=True)


def test_excludes_apply(setup: Setup, cli: Cli) -> None:
    src, repo = setup(exclude=["*.tmp", "/sub/"])
    (src / "x.tmp").write_text("t")
    assert cli("run", "docs") == 0
    (snap,) = snapshots(repo)
    assert not (snap / "files/docs/x.tmp").exists()
    assert not (snap / "files/docs/sub").exists()
    assert (snap / "files/docs/a.txt").exists()


def test_label_and_pin_in_manifest(setup: Setup, cli: Cli) -> None:
    _, repo = setup()
    assert cli("run", "docs", "--label", "before upgrade", "--pin") == 0
    m = manifest(snapshots(repo)[0])
    assert m["label"] == "before upgrade"
    assert m["pinned_at_creation"] is True
    assert m["timings"]["total"] >= 0


def test_resume_reuses_unfinished_run(setup: Setup, cli: Cli) -> None:
    _, repo = setup()
    leftover = repo / "targets/docs/.incomplete-2026-01-01T10-00-00"
    (leftover / "files/docs").mkdir(parents=True)
    os.utime(leftover, None)
    # make it recent enough to be resumed
    from datetime import datetime

    name = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    recent = leftover.with_name(f".incomplete-{name}")
    leftover.rename(recent)
    assert cli("run", "docs") == 0
    (snap,) = snapshots(repo)
    assert snap.name == name
    assert manifest(snap)["resumed"] == 1


def test_old_unfinished_run_is_set_aside(setup: Setup, cli: Cli) -> None:
    _, repo = setup()
    old = repo / "targets/docs/.incomplete-2020-01-01T10-00-00"
    old.mkdir(parents=True)
    assert cli("run", "docs") == 0
    assert (repo / "targets/docs/.abandoned-2020-01-01T10-00-00").exists()


def test_locked_repository_is_refused(setup: Setup, cli: Cli) -> None:
    _, repo = setup()
    (repo / LOCK_NAME).write_text(
        json.dumps({"host": "elsewhere", "pid": 1, "started": "x", "command": "eichhorn run"})
    )
    assert cli("run", "docs") == 1  # the target fails, nothing written
    assert snapshots(repo) == []


def test_unknown_target(setup: Setup, cli: Cli, tmp_path: Path) -> None:
    setup()
    assert cli("run", "nope") == 2


def test_status_and_snapshots_json(
    setup: Setup, cli: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    setup()
    assert cli("run", "docs") == 0
    capsys.readouterr()
    assert cli("--json", "status") == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["target"] == "docs" and data[0]["last"]["status"] == "ok"
    assert cli("--json", "snapshots", "docs") == 0
    snaps = json.loads(capsys.readouterr().out)
    assert len(snaps) == 1 and snaps[0]["repository_name"] == "Backup-A"


def test_config_check(setup: Setup, cli: Cli, tmp_path: Path) -> None:
    setup()
    assert cli("config", "check") == 0
    (tmp_path / "cfg/targets/bad.yaml").write_text("sorces: []\n")
    assert cli("config", "check") == 2


def test_large_file_resumes_from_offset(tmp_path: Path) -> None:
    from eichhorn.engines import builtin

    src = tmp_path / "s"
    src.mkdir()
    data = os.urandom(300_000)
    (src / "big.bin").write_bytes(data)
    dest = tmp_path / "d"
    (dest / "files/s").mkdir(parents=True)
    caps = StoreCapabilities(True, True, True, None)
    eng = BuiltinEngine(caps)
    old = builtin.RESUME_THRESHOLD
    builtin.RESUME_THRESHOLD = 1000
    try:
        st = (src / "big.bin").stat()
        partial = dest / "files/s/big.bin.eichhorn-partial"
        partial.write_bytes(data[:100_000])
        partial.with_name(partial.name + ".src").write_text(f"{st.st_size} {st.st_mtime_ns}")
        entries, stats = eng.transfer(
            LocalConnection(), Source("s", str(src)), dest, Previous(None, {})
        )
    finally:
        builtin.RESUME_THRESHOLD = old
    assert (dest / "files/s/big.bin").read_bytes() == data
    assert stats.transferred_bytes == 200_000
    import hashlib

    assert (
        next(e for e in entries if e.p.endswith("big.bin")).sha256
        == hashlib.sha256(data).hexdigest()
    )


def test_split_files_for_fat32(tmp_path: Path) -> None:
    src = tmp_path / "s"
    src.mkdir()
    data = os.urandom(2500)
    (src / "video.mp4").write_bytes(data)
    dest = tmp_path / "d"
    (dest / "files/s").mkdir(parents=True)
    eng = BuiltinEngine(StoreCapabilities(False, False, False, 1000))
    entries, stats = eng.transfer(
        LocalConnection(), Source("s", str(src)), dest, Previous(None, {})
    )
    parts = sorted((dest / "files/s").glob("video.mp4.eichhorn-part-*"))
    assert len(parts) == 3
    assert b"".join(p.read_bytes() for p in parts) == data
    assert next(e for e in entries if e.p.endswith("video.mp4")).parts == 3
    assert stats.split == 1

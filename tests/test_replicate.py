"""Milestone 4: several repositories per run, repo sync."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from eichhorn.stores.localfs import LocalRepository

from .conftest import touch_later

Cli = Callable[..., int]


def setup_two(tmp_path: Path, cli: Cli, b_storage: str = "sparse") -> tuple[Path, Path, Path]:
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "a.txt").write_text("alpha")
    (src / "sub" / "b.txt").write_text("beta")
    a = tmp_path / "drive-a" / "eichhorn"
    b = tmp_path / "drive-b" / "eichhorn"
    assert cli("repo", "init", str(a), "--name", "A") == 0
    assert cli("repo", "init", str(b), "--name", "B", "--storage", b_storage) == 0
    t = tmp_path / "cfg" / "targets"
    t.mkdir(parents=True)
    (t / "docs.yaml").write_text(f"sources:\n  - name: docs\n    path: '{src}'\n")
    return src, a, b


def snaps(repo: Path) -> list[str]:
    return LocalRepository.open(repo).snapshots("docs")


def manifest(repo: Path, snap: str) -> dict:  # type: ignore[type-arg]
    return json.loads((repo / "targets/docs/snapshots" / snap / "snapshot.json").read_text())


def test_several_connected_need_a_choice(tmp_path: Path, cli: Cli) -> None:
    setup_two(tmp_path, cli)
    assert cli("run", "docs") == 3  # not interactive: --repo or --all-repos needed


def test_run_to_all_repos(tmp_path: Path, cli: Cli) -> None:
    src, a, b = setup_two(tmp_path, cli)
    assert cli("run", "docs", "--all-repos", "--label", "first") == 0
    assert snaps(a) == snaps(b)
    snap = snaps(b)[0]
    m = manifest(b, snap)
    assert m["copied_from"] == LocalRepository.open(a).id
    assert m["repository_name"] == "B" and m["storage"] == "sparse"
    assert m["label"] == "first"
    assert m["copy"]["copied"] == 2
    touch_later(src / "a.txt", "alpha 2")
    assert cli("run", "docs", "--repo", "A", "--repo", "B") == 0
    second = snaps(b)[-1]
    m2 = manifest(b, second)
    assert m2["copy"]["copied"] == 1  # only the changed file
    assert not (b / "targets/docs/snapshots" / second / "files/docs/sub/b.txt").exists()
    assert cli("verify", "docs", "--all-snapshots", "--repo", "B") == 0
    out = tmp_path / "out"
    assert cli("restore", "docs", "latest", "--repo", "B", "--to", str(out)) == 0
    assert (out / "docs/a.txt").read_text() == "alpha 2"
    assert (out / "docs/sub/b.txt").read_text() == "beta"


def test_repo_sync(tmp_path: Path, cli: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    src, a, b = setup_two(tmp_path, cli, b_storage="hardlinks")
    for i in range(3):
        touch_later(src / "c.txt", f"v{i}")
        assert cli("run", "docs", "--repo", "A") == 0
    first = snaps(a)[0]
    assert cli("pin", "docs", first, "--repo", "A", "--note", "keep") == 0
    assert cli("repo", "sync", "A", "B", "--dry-run") == 0
    assert snaps(b) == []
    assert cli("repo", "sync", "A", "B") == 0
    assert snaps(b) == snaps(a)
    sb = b / "targets/docs/snapshots"
    ino = [(sb / s / "files/docs/a.txt").stat().st_ino for s in snaps(b)]
    assert len(set(ino)) == 1  # unchanged file hardlinked across the copied snapshots
    capsys.readouterr()
    assert cli("--json", "snapshots", "docs", "--repo", "B") == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["pinned"] is True and data[0]["note"] == "keep"
    assert cli("verify", "docs", "--all-snapshots", "--repo", "B") == 0


def test_repo_sync_both_and_older(tmp_path: Path, cli: Cli) -> None:
    src, a, b = setup_two(tmp_path, cli, b_storage="hardlinks")
    assert cli("run", "docs", "--repo", "A") == 0  # older, only in A
    time.sleep(1.1)  # snapshot names have a resolution of one second
    touch_later(src / "c.txt", "x")
    assert cli("run", "docs", "--repo", "B") == 0  # newer, only in B
    assert cli("repo", "sync", "A", "B", "--both") == 0
    assert len(snaps(a)) == 2  # A got B's newer snapshot
    assert len(snaps(b)) == 1  # A's snapshot is older than B's oldest: skipped
    assert cli("repo", "sync", "A", "B", "--include-older") == 0
    assert snaps(a) == snaps(b)
    assert (b / "targets/docs/latest").read_text().strip() == snaps(b)[-1]


def test_copy_splits_for_small_max_size(tmp_path: Path, cli: Cli) -> None:
    _, _, b = setup_two(tmp_path, cli)
    meta = b / "repository.yaml"
    meta.write_text(meta.read_text().replace("max_file_size: null", "max_file_size: 2"))
    assert cli("run", "docs", "--all-repos") == 0
    snap = snaps(b)[0]
    folder = b / "targets/docs/snapshots" / snap / "files/docs"
    parts = sorted(folder.glob("a.txt.eichhorn-part-*"))
    assert [p.read_text() for p in parts] == ["al", "ph", "a"]
    out = tmp_path / "out"
    assert cli("restore", "docs", "latest", "--repo", "B", "--to", str(out)) == 0
    assert (out / "docs/a.txt").read_text() == "alpha"
    assert cli("verify", "docs", "--repo", "B") == 0

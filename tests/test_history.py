"""Milestone 3: retention, prune, pins, labels, verify, restore, diff, find."""

from __future__ import annotations

import json
import os
import shlex
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest

from eichhorn.content import Resolver
from eichhorn.retention import SnapInfo, plan
from eichhorn.stores.localfs import LocalRepository

from .conftest import touch_later

Cli = Callable[..., int]
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes and symlinks")


# ---------------------------------------------------------------- helpers
def make(tmp_path: Path, cli: Cli, storage: str | None = None, retention: str = "") -> Path:
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True, exist_ok=True)
    (src / "a.txt").write_text("alpha")
    (src / "sub" / "b.txt").write_text("beta")
    repo = tmp_path / "drive" / "eichhorn"
    args = ["repo", "init", str(repo), "--name", "Backup-A"]
    if storage:
        args += ["--storage", storage]
    assert cli(*args) == 0
    t = tmp_path / "cfg" / "targets"
    t.mkdir(parents=True, exist_ok=True)
    (t / "docs.yaml").write_text(f"sources:\n  - name: docs\n    path: '{src}'\n{retention}")
    return repo


def run_n(cli: Cli, src: Path, n: int) -> None:
    """n runs; each one changes c.txt, so every snapshot differs."""
    for i in range(n):
        touch_later(src / "c.txt", f"version {i}")
        assert cli("run", "docs", "--no-prune") == 0


def snaps(repo: Path) -> list[str]:
    return LocalRepository.open(repo).snapshots("docs")


def renamed_snapshots(repo: Path, names: list[str]) -> None:
    """Give the snapshots of `docs` the given names (to test date-based rules)."""
    d = repo / "targets" / "docs" / "snapshots"
    for old, new in zip(sorted(os.listdir(d)), names, strict=True):
        m = json.loads((d / old / "snapshot.json").read_text())
        m["snapshot"] = new
        (d / old / "snapshot.json").write_text(json.dumps(m))
        (d / old).rename(d / new)
    (repo / "targets" / "docs" / "latest").write_text(names[-1])


# ---------------------------------------------------------------- retention
def info(name: str, status: str = "ok", pinned: bool = False) -> SnapInfo:
    return SnapInfo(name, datetime.strptime(name, "%Y-%m-%dT%H-%M-%S"), status, pinned)


def test_retention_rules() -> None:
    names = [
        "2026-01-05T10-00-00", "2026-01-20T10-00-00", "2026-02-03T10-00-00",
        "2026-02-04T10-00-00", "2026-03-01T10-00-00", "2026-03-01T18-00-00",
    ]  # fmt: skip
    snaps_ = [info(n) for n in names]
    keep = plan(snaps_, {"keep_last": 2})
    assert sorted(keep) == names[-2:]
    keep = plan(snaps_, {"keep_monthly": 2})
    assert sorted(keep) == ["2026-02-04T10-00-00", "2026-03-01T18-00-00"]
    keep = plan(snaps_, {"keep_daily": 3})
    assert sorted(keep) == ["2026-02-03T10-00-00", "2026-02-04T10-00-00", "2026-03-01T18-00-00"]
    assert len(plan(snaps_, {})) == 6  # no rules: keep everything


def test_retention_pins_and_newest_ok() -> None:
    a = info("2026-01-01T00-00-00", pinned=True)
    b = info("2026-01-02T00-00-00")
    c = info("2026-01-03T00-00-00", status="partial")
    keep = plan([a, b, c], {"keep_last": 1})
    assert keep[a.name] == ["pinned"]
    assert "newest ok" in keep[b.name]  # the partial one is newer, but not ok
    assert keep[c.name] == ["last"]


# ---------------------------------------------------------------- prune
def test_prune_hardlinks(tmp_path: Path, cli: Cli) -> None:
    repo = make(tmp_path, cli, retention="retention:\n  keep_last: 2\n")
    run_n(cli, tmp_path / "src", 4)
    first = snaps(repo)[0]
    assert cli("pin", "docs", first, "--note", "keep me") == 0
    assert cli("prune", "--dry-run") == 0
    assert len(snaps(repo)) == 4
    assert cli("prune") == 0
    left = snaps(repo)
    assert len(left) == 3 and left[0] == first  # pinned + last 2
    newest = repo / "targets/docs/snapshots" / left[-1]
    assert (newest / "files/docs/a.txt").read_text() == "alpha"
    assert cli("verify", "docs", "--all-snapshots") == 0


def test_prune_after_run(tmp_path: Path, cli: Cli) -> None:
    repo = make(tmp_path, cli, retention="retention:\n  keep_last: 2\n")
    src = tmp_path / "src"
    for i in range(3):
        touch_later(src / "c.txt", f"v{i}")
        assert cli("run", "docs") == 0
    assert len(snaps(repo)) == 2


def test_prune_sparse_moves_needed_files_forward(tmp_path: Path, cli: Cli) -> None:
    repo = make(tmp_path, cli, storage="sparse", retention="retention:\n  keep_last: 1\n")
    run_n(cli, tmp_path / "src", 3)
    first, _, last = snaps(repo)
    base = repo / "targets/docs/snapshots"
    assert (base / first / "files/docs/a.txt").exists()
    assert not (base / last / "files/docs/a.txt").exists()  # stored only in the first
    assert cli("prune") == 0
    assert snaps(repo) == [last]
    assert (base / last / "files/docs/a.txt").read_text() == "alpha"  # moved forward
    assert "files/docs/a.txt" in (base / last / "checksums.sha256").read_text()
    assert cli("verify", "docs") == 0
    out = tmp_path / "restored"
    assert cli("restore", "docs", "latest", "--to", str(out)) == 0
    assert (out / "docs/sub/b.txt").read_text() == "beta"


def test_interrupted_prune_is_finished(tmp_path: Path, cli: Cli) -> None:
    from eichhorn import prune

    repo_path = make(tmp_path, cli, storage="sparse")
    run_n(cli, tmp_path / "src", 2)
    first, last = snaps(repo_path)
    repo = LocalRepository.open(repo_path)
    journal = {"from": first, "to": last, "paths": ["files/docs/a.txt", "files/docs/sub/b.txt"]}
    (repo.target_dir("docs") / prune.JOURNAL).write_text(json.dumps(journal))
    # simulate a crash after moving one of the two files
    base = repo_path / "targets/docs/snapshots"
    (base / last / "files/docs").mkdir(parents=True, exist_ok=True)
    os.replace(base / first / "files/docs/a.txt", base / last / "files/docs/a.txt")
    assert cli("run", "docs", "--no-prune") == 0  # a run finishes the prune first
    assert first not in snaps(repo_path)
    assert cli("verify", "docs", "--all-snapshots") == 0


# ---------------------------------------------------------------- pins and labels
def test_pin_label_in_snapshots(
    tmp_path: Path, cli: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    make(tmp_path, cli)
    assert cli("run", "docs", "--label", "first") == 0
    assert cli("label", "docs", "latest", "renamed") == 0
    assert cli("pin", "docs", "latest") == 0
    capsys.readouterr()
    assert cli("--json", "snapshots", "docs") == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["label"] == "renamed" and data[0]["pinned"] is True
    assert cli("unpin", "docs", "latest") == 0
    assert cli("label", "docs", "latest", "") == 0
    assert cli("--json", "snapshots", "docs") == 0
    data = json.loads(_last_json(capsys))
    assert data[0]["label"] is None and data[0]["pinned"] is False


def _last_json(capsys: pytest.CaptureFixture[str]) -> str:
    out = capsys.readouterr().out
    return out[out.index("[") :]


# ---------------------------------------------------------------- verify
def test_verify_finds_problems(
    tmp_path: Path, cli: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make(tmp_path, cli)
    assert cli("run", "docs") == 0
    assert cli("verify", "docs") == 0
    snap = repo / "targets/docs/snapshots" / snaps(repo)[0]
    (snap / "files/docs/a.txt").write_text("ALPHA")  # bit rot
    (snap / "files/docs/sub/b.txt").unlink()
    (snap / "files/docs/stray.txt").write_text("?")
    capsys.readouterr()
    assert cli("--json", "verify", "docs") == 1
    rep = json.loads(capsys.readouterr().out)[0]
    assert [c["path"] for c in rep["corrupted"]] == ["files/docs/a.txt"]
    assert [m["path"] for m in rep["missing"]] == ["files/docs/sub/b.txt"]
    assert [u["path"] for u in rep["unexpected"]] == ["files/docs/stray.txt"]
    assert list((repo / "logs/verify/docs").glob("*.json"))


def test_verify_sample_and_against_source(
    tmp_path: Path, cli: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    make(tmp_path, cli)
    assert cli("run", "docs") == 0
    assert cli("verify", "docs", "--sample", "50") == 0
    src = tmp_path / "src"
    st = (src / "a.txt").stat()
    (src / "a.txt").write_text("ALPHA")  # same size, same time, other content
    os.utime(src / "a.txt", ns=(st.st_atime_ns, st.st_mtime_ns))
    (src / "new.txt").write_text("n")
    capsys.readouterr()
    assert cli("--json", "verify", "docs", "--against-source") == 1
    source = json.loads(capsys.readouterr().out)[0]["source"]
    assert source["new"] == 1
    assert [d["path"] for d in source["differs"]] == ["files/docs/a.txt"]


# ---------------------------------------------------------------- restore
@posix_only
def test_restore_whole_and_part(tmp_path: Path, cli: Cli) -> None:
    make(tmp_path, cli)
    src = tmp_path / "src"
    os.chmod(src / "a.txt", 0o600)
    os.symlink("a.txt", src / "link")
    assert cli("run", "docs") == 0
    out = tmp_path / "out"
    assert cli("restore", "docs", "latest", "--to", str(out)) == 0
    assert (out / "docs/a.txt").read_text() == "alpha"
    assert (out / "docs/a.txt").stat().st_mode & 0o777 == 0o600
    assert (out / "docs/a.txt").stat().st_mtime_ns == (src / "a.txt").stat().st_mtime_ns
    assert os.readlink(out / "docs/link") == "a.txt"
    one = tmp_path / "one"
    assert cli("restore", "docs", "latest", "docs/sub", "--to", str(one)) == 0
    assert (one / "sub/b.txt").read_text() == "beta"
    assert cli("restore", "docs", "latest", "docs/sub", "--to", str(one)) == 1  # exists
    assert cli("restore", "docs", "latest", "docs/sub", "--to", str(one), "--force") == 0
    assert cli("restore", "docs", "latest", "nope", "--to", str(one)) == 1


def test_restore_old_version_from_sparse(tmp_path: Path, cli: Cli) -> None:
    make(tmp_path, cli, storage="sparse")
    src = tmp_path / "src"
    assert cli("run", "docs") == 0
    touch_later(src / "a.txt", "alpha 2")
    assert cli("run", "docs") == 0
    first, second = snaps(tmp_path / "drive/eichhorn")
    old = tmp_path / "old"
    assert cli("restore", "docs", first, "--to", str(old)) == 0
    assert (old / "docs/a.txt").read_text() == "alpha"
    new = tmp_path / "new"
    assert cli("restore", "docs", second, "--to", str(new)) == 0
    assert (new / "docs/a.txt").read_text() == "alpha 2"
    assert (new / "docs/sub/b.txt").read_text() == "beta"  # stored only in the first


def test_restore_original_names(tmp_path: Path, cli: Cli) -> None:
    repo = make(tmp_path, cli)
    meta = repo / "repository.yaml"
    meta.write_text(meta.read_text().replace("posix_metadata: true", "posix_metadata: false"))
    if sys.platform == "win32":
        pytest.skip("cannot create such a file on Windows")
    (tmp_path / "src" / "a:b.txt").write_text("colon")
    assert cli("run", "docs") == 0
    snap = repo / "targets/docs/snapshots" / snaps(repo)[0]
    assert (snap / "files/docs/a%3Ab.txt").read_text() == "colon"
    out = tmp_path / "out"
    assert cli("restore", "docs", "latest", "--to", str(out)) == 0
    assert (out / "docs/a:b.txt").read_text() == "colon"


@posix_only
def test_restore_onto_server(tmp_path: Path, cli: Cli, monkeypatch: pytest.MonkeyPatch) -> None:
    shim = Path(__file__).with_name("fake_ssh.py")
    monkeypatch.setenv("EICHHORN_SSH", f"{shlex.quote(sys.executable)} {shlex.quote(str(shim))}")
    make(tmp_path, cli)
    assert cli("run", "docs") == 0
    dest = tmp_path / "server-restore"
    assert cli("restore", "docs", "latest", "docs", "--to", f"fakehost:{dest}") == 0
    assert (dest / "docs/sub/b.txt").read_text() == "beta"
    assert cli("restore", "docs", "latest", "docs", "--to", f"fakehost:{dest}") == 1  # not empty


# ---------------------------------------------------------------- diff and find
def test_diff_and_find(tmp_path: Path, cli: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    make(tmp_path, cli)
    src = tmp_path / "src"
    assert cli("run", "docs") == 0
    touch_later(src / "a.txt", "alpha 2")
    (src / "sub" / "b.txt").unlink()
    (src / "c.txt").write_text("gamma")
    assert cli("run", "docs") == 0
    touch_later(src / "a.txt", "alpha 3")
    assert cli("run", "docs") == 0
    capsys.readouterr()
    assert cli("--json", "diff", "docs") == 0
    d = json.loads(capsys.readouterr().out)
    assert [x["path"] for x in d["changed"]] == ["files/docs/a.txt"]
    first = snaps(tmp_path / "drive/eichhorn")[0]
    assert cli("--json", "diff", "docs", first, "latest") == 0
    d = json.loads(capsys.readouterr().out)
    assert [x["path"] for x in d["added"]] == ["files/docs/c.txt"]
    assert [x["path"] for x in d["removed"]] == ["files/docs/sub/b.txt"]
    assert cli("--json", "find", "docs", "A.TXT") == 0
    found = json.loads(capsys.readouterr().out)
    assert found[0]["path"] == "files/docs/a.txt"
    assert len(found[0]["versions"]) == 3
    assert cli("find", "docs", "nothing-like-this") == 1


def test_resolver_prefix_names(tmp_path: Path, cli: Cli) -> None:
    repo = make(tmp_path, cli)
    assert cli("run", "docs") == 0
    r = Resolver(LocalRepository.open(repo), "docs")
    name = r.snapshots[0]
    assert r.resolve_name(name[:13]) == name
    assert r.resolve_name("latest") == name


def test_verify_with_absolute_long_paths(
    tmp_path: Path, cli: Cli, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On Windows, long_path() turns paths into absolute \\\\?\\ paths (regression test)."""
    make(tmp_path, cli)
    assert cli("run", "docs") == 0
    monkeypatch.setattr("eichhorn.content.long_path", lambda p: Path(os.path.abspath(p)))
    assert cli("verify", "docs") == 0

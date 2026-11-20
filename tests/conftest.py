from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from eichhorn.cli import main


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test gets its own config and state folder, and no colours."""
    monkeypatch.setenv("EICHHORN_CONFIG", str(tmp_path / "cfg"))
    monkeypatch.setenv("EICHHORN_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("NO_COLOR", "1")
    # never look at real mounted volumes in tests
    monkeypatch.setattr("eichhorn.repository.volume_roots", lambda: [])


@pytest.fixture
def cli() -> Callable[..., int]:
    def run(*args: str) -> int:
        return main(["--allow-sleep", *args])

    return run


@pytest.fixture
def setup(tmp_path: Path, cli: Callable[..., int]) -> Callable[..., tuple[Path, Path]]:
    """Create a source folder, a repository and a target file. Returns (src, repo)."""

    def make(storage: str | None = None, exclude: list[str] | None = None) -> tuple[Path, Path]:
        src = tmp_path / "src"
        (src / "sub").mkdir(parents=True)
        (src / "a.txt").write_text("alpha")
        (src / "sub" / "b.txt").write_text("beta")
        repo = tmp_path / "drive" / "eichhorn"
        args = ["repo", "init", str(repo), "--name", "Backup-A"]
        if storage:
            args += ["--storage", storage]
        assert cli(*args) == 0
        targets = tmp_path / "cfg" / "targets"
        targets.mkdir(parents=True, exist_ok=True)
        ex = "".join(f"\n      - '{e}'" for e in exclude or [])
        (targets / "docs.yaml").write_text(
            f"sources:\n  - name: docs\n    path: '{src}'\n    exclude:{ex or ' []'}\n"
        )
        return src, repo

    return make


def touch_later(path: Path, text: str) -> None:
    """Write and make sure the mtime differs from before."""
    path.write_text(text)
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
    time.sleep(0.01)
